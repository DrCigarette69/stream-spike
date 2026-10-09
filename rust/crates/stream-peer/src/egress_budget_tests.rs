//! A4.4 stream cap + persistent byte budget tests (temp dirs, loopback only).
use crate::egress_budget::{budget_path, day_str, load, save, Loaded};
use crate::egress_state::watchdog_step;
use crate::pilot_egress::*;
use crate::pilot_egress_tests::{pilot_state, plane, Fake};
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use stream_proto::guard::Lane;

fn tmpdir(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("pe-budget-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn mode(p: &std::path::Path) -> u32 {
    std::fs::metadata(p).unwrap().permissions().mode() & 0o777
}

#[test]
fn day_str_and_default_path() {
    assert_eq!(day_str(0), "1970-01-01");
    assert_eq!(day_str(11016), "2000-02-29");
    assert_eq!(day_str(19723), "2024-01-01");
    assert_eq!(day_str(20735), "2026-10-09");
    if std::env::var(crate::egress_budget::ENV_BUDGET_PATH).is_err() {
        assert_eq!(budget_path("/x/y/.peer_iroh_key"), PathBuf::from("/x/y/.peer_egress_budget"));
        assert_eq!(budget_path(".peer_iroh_key"), PathBuf::from("./.peer_egress_budget"));
    }
    assert_eq!(max_streams_from(None), 2);
    assert_eq!(max_streams_from(Some("0")), 1);
    assert_eq!(max_streams_from(Some("99")), 16);
    assert_eq!(max_streams_from(Some("x")), 2);
}

#[tokio::test]
async fn third_stream_refused_no_p8() {
    let r = Fake::with(&[("site1.pilot.example", &[&["93.184.216.34"]])]);
    let p = plane(r, 5000, 1 << 20, Lane::Pilot);
    assert!(check_and_pin(&p, "s1", "site1.pilot.example", 443).await.is_ok());
    assert!(check_and_pin(&p, "s2", "site1.pilot.example", 443).await.is_ok());
    let e = check_and_pin(&p, "s3", "site1.pilot.example", 443).await.unwrap_err();
    assert_eq!(e.reason, "egress_stream_limit");
    assert_eq!(e.line, "a4_refuse_egress_stream_limit:2");
    let st = pilot_state(&p);
    watchdog_step(&st, &p).await;
    assert_eq!(st.read().await.p8_reason, None, "stream cap never P8");
    p.lock().unwrap().close_one("s1", "gateway");
    assert!(check_and_pin(&p, "s3", "site1.pilot.example", 443).await.is_ok(), "slot freed");
    // A refusal on an already reserved sid keeps that reservation.
    assert!(check_and_pin(&p, "s2", "site1.pilot.example", 80).await.is_err());
    assert_eq!(check_and_pin(&p, "s4", "site1.pilot.example", 443).await.unwrap_err().reason, "egress_stream_limit");
}

#[test]
fn counter_survives_restart_missing_is_zero_mode_0600_atomic() {
    let d = tmpdir("restart");
    let f = d.join(".peer_egress_budget");
    let a = plane(Fake::with(&[]), 5000, 10_000, Lane::Pilot);
    a.lock().unwrap().attach_store(f.clone());
    assert_eq!(a.lock().unwrap().used, 0, "missing file -> 0");
    a.lock().unwrap().add_bytes(1500).unwrap();
    a.lock().unwrap().flush(true);
    assert_eq!(mode(&f), 0o600);
    let names: Vec<String> = std::fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
    assert_eq!(names, vec![".peer_egress_budget".to_string()], "no temp file left: {names:?}");
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&f).unwrap()).unwrap();
    assert_eq!(v["bytes"], 1500);
    assert_eq!(v["utc_day"], day_str(utc_day()));
    assert_eq!(v["allowlist_version"], a.lock().unwrap().version.as_str());
    let b = plane(Fake::with(&[]), 5000, 10_000, Lane::Pilot);
    b.lock().unwrap().attach_store(f.clone());
    assert_eq!(b.lock().unwrap().used, 1500, "restart keeps the count");
    // Failed write (target is a directory): no partial / temp file, fail closed.
    let bad = d.join("is_a_dir");
    std::fs::create_dir(&bad).unwrap();
    assert!(save(&bad, utc_day(), 1, "v").is_err());
    let left = std::fs::read_dir(&d).unwrap().filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().contains(".tmp.")).count();
    assert_eq!(left, 0);
    let _ = std::fs::remove_dir_all(&d);
}

#[tokio::test]
async fn corrupt_perms_and_cap_each_show_their_own_line() {
    let d = tmpdir("corrupt");
    let f = d.join(".peer_egress_budget");
    let cases: [(&str, u32, &str, &str); 3] = [
        ("{not json", 0o600, "egress_budget_unreadable", "state_corrupt"),
        ("{\"utc_day\":\"2026-10-09\",\"bytes\":1}", 0o644, "egress_budget_unreadable", "state_unreadable"),
        ("{\"bytes\":\"x\"}", 0o600, "egress_budget_unreadable", "state_corrupt"),
    ];
    for (body, m, key, detail) in cases {
        std::fs::write(&f, body).unwrap();
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(m)).unwrap();
        assert_eq!(load(&f, utc_day()), Loaded::Unreadable(detail));
        let p = plane(Fake::with(&[]), 300, 10_000, Lane::Pilot);
        p.lock().unwrap().attach_store(f.clone());
        let e = p.lock().unwrap().status().unwrap_err();
        assert_eq!((p8_key(&e), e.detail.as_str()), (key, detail));
        assert!(p.lock().unwrap().add_bytes(1).is_err(), "fail closed");
        assert_eq!(std::fs::read_to_string(&f).unwrap(), body, "bad file not overwritten same day");
        let st = pilot_state(&p);
        tokio::time::sleep(std::time::Duration::from_millis(350)).await;
        let v = p.lock().unwrap().version.clone();
        p.lock().unwrap().update_state(true, &v); // keep the switch fresh past the grace
        watchdog_step(&st, &p).await;
        assert_eq!(st.read().await.p8_reason.as_deref(), Some(key));
    }
    let p = plane(Fake::with(&[]), 300, 100, Lane::Pilot);
    assert!(p.lock().unwrap().add_bytes(101).is_err());
    let e = p.lock().unwrap().status().unwrap_err();
    assert_eq!(p8_key(&e), "egress_budget_exceeded");
    let over = crate::a4_copy::p8_reason_line("egress_budget_exceeded").unwrap();
    let unread = crate::a4_copy::p8_reason_line("egress_budget_unreadable").unwrap();
    assert_ne!(over, unread);
    assert!(over.contains("reached today's sharing limit"));
    assert!(unread.contains("couldn't check today's sharing limit"));
    let _ = std::fs::remove_dir_all(&d);
}

static DAY: AtomicU64 = AtomicU64::new(20735);
fn test_day() -> u64 {
    DAY.load(Ordering::SeqCst)
}

#[test]
fn utc_rollover_resets_and_heals_bad_file() {
    let d = tmpdir("rollover");
    let f = d.join(".peer_egress_budget");
    std::fs::write(&f, "garbage").unwrap();
    std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o600)).unwrap();
    let p = plane(Fake::with(&[]), 5000, 10_000, Lane::Pilot);
    p.lock().unwrap().day_fn = test_day;
    p.lock().unwrap().attach_store(f.clone());
    assert!(p.lock().unwrap().status().is_err());
    DAY.store(20736, Ordering::SeqCst);
    assert!(p.lock().unwrap().status().is_ok(), "new UTC day: fresh budget");
    let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&f).unwrap()).unwrap();
    assert_eq!((v["utc_day"].as_str(), v["bytes"].as_u64()), (Some("2026-10-10"), Some(0)));
    assert_eq!(mode(&f), 0o600);
    p.lock().unwrap().add_bytes(9_000).unwrap();
    p.lock().unwrap().flush(true);
    DAY.store(20737, Ordering::SeqCst);
    assert!(p.lock().unwrap().add_bytes(9_000).is_ok(), "counter reset at midnight");
    assert_eq!(load(&f, 20738), Loaded::Bytes(0), "older day in file -> 0");
    let _ = std::fs::remove_dir_all(&d);
}
