//! Redial backoff (Architect's Peer contract): every session end, including a clean remote close,
//! waits before redialing; never an immediate redial. Exponential backoff with full jitter:
//! attempt n waits uniform[floor, min(cap, base * 2^(n-1))], floor = base / 2 (so never ~0).
//! The attempt counter resets only after a session stayed up for `reset_after`.
//! Env (for tests / ops): SPIKE_REDIAL_BASE_MS (2000), SPIKE_REDIAL_CAP_MS (300000),
//! SPIKE_REDIAL_RESET_SECS (60).
#![cfg_attr(not(feature = "iroh_pilot"), allow(dead_code))]
use std::time::Duration;

pub const DEFAULT_BASE_MS: u64 = 2_000;
pub const DEFAULT_CAP_MS: u64 = 300_000;
pub const DEFAULT_RESET_SECS: u64 = 60;

#[derive(Debug, Clone)]
pub struct Backoff {
    base_ms: u64,
    cap_ms: u64,
    reset_after: Duration,
    attempt: u32,
    rng: u64,
    reset_pending: bool,
}

fn env_u64(key: &str, default: u64) -> u64 {
    std::env::var(key).ok().and_then(|v| v.trim().parse().ok()).filter(|v| *v > 0).unwrap_or(default)
}

impl Backoff {
    pub fn new(base_ms: u64, cap_ms: u64, reset_after: Duration, seed: u64) -> Self {
        let base_ms = base_ms.max(1);
        Backoff { base_ms, cap_ms: cap_ms.max(base_ms), reset_after, attempt: 0, rng: seed | 1, reset_pending: false }
    }

    pub fn from_env() -> Self {
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0)
            ^ ((std::process::id() as u64) << 32);
        Self::new(
            env_u64("SPIKE_REDIAL_BASE_MS", DEFAULT_BASE_MS),
            env_u64("SPIKE_REDIAL_CAP_MS", DEFAULT_CAP_MS),
            Duration::from_secs(env_u64("SPIKE_REDIAL_RESET_SECS", DEFAULT_RESET_SECS)),
            seed,
        )
    }

    fn next_rand(&mut self) -> u64 {
        // xorshift64* (no extra crate; jitter only, not security-relevant)
        let mut x = self.rng;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.rng = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// Upper bound for attempt `n` (1-based): min(cap, base * 2^(n-1)).
    pub fn ceiling_ms(&self, n: u32) -> u64 {
        let shift = n.saturating_sub(1).min(32);
        self.base_ms.saturating_mul(1u64 << shift).min(self.cap_ms)
    }

    pub fn floor_ms(&self) -> u64 {
        (self.base_ms / 2).max(1)
    }

    /// Next wait: (attempt number, delay ms).
    pub fn next_delay(&mut self) -> (u32, u64) {
        self.attempt = self.attempt.saturating_add(1);
        let hi = self.ceiling_ms(self.attempt);
        let lo = self.floor_ms().min(hi);
        let ms = lo + self.next_rand() % (hi - lo + 1);
        (self.attempt, ms)
    }

    /// Terminal refusal (peer_id_in_use / endpoint_mismatch): no fast redials, wait the cap.
    /// Moves the attempt counter to where the ceiling reaches the cap; returns (attempt, cap_ms).
    pub fn jump_to_cap(&mut self) -> (u32, u64) {
        let mut n = self.attempt.max(1);
        while self.ceiling_ms(n) < self.cap_ms && n < 64 {
            n += 1;
        }
        self.attempt = n;
        (n, self.cap_ms)
    }

    /// Call at every session end with how long it was up; resets only after a stable session.
    pub fn session_ended(&mut self, up_for: Duration) {
        if up_for >= self.reset_after {
            self.attempt = 0;
            self.reset_pending = true;
        }
    }

    /// True once after a stable-session reset (for `reset=1` on the next redial line).
    pub fn take_reset(&mut self) -> bool {
        std::mem::take(&mut self.reset_pending)
    }

    pub fn attempt(&self) -> u32 {
        self.attempt
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bo() -> Backoff {
        Backoff::new(2_000, 300_000, Duration::from_secs(60), 42)
    }

    #[test]
    fn schedule_grows_then_caps() {
        let b = bo();
        let c: Vec<u64> = (1..=10).map(|n| b.ceiling_ms(n)).collect();
        assert_eq!(c, vec![2_000, 4_000, 8_000, 16_000, 32_000, 64_000, 128_000, 256_000, 300_000, 300_000]);
        assert_eq!(b.ceiling_ms(1000), 300_000, "no overflow far past the cap");
    }

    #[test]
    fn jitter_in_bounds_and_never_immediate() {
        let mut b = bo();
        let mut max_seen = 0;
        for _ in 0..200 {
            let (n, ms) = b.next_delay();
            assert!(ms >= 1_000 && ms <= b.ceiling_ms(n), "attempt {n}: {ms}");
            assert!(ms <= 300_000);
            max_seen = max_seen.max(ms);
        }
        assert!(max_seen > 150_000, "jitter spreads toward the cap: {max_seen}");
        let ms: Vec<u64> = (0..50).map(|_| b.next_delay().1).collect();
        assert!(ms.iter().any(|&m| m != ms[0]), "jitter varies");
    }

    #[test]
    fn seeded_is_deterministic() {
        let (mut a, mut b) = (bo(), bo());
        for _ in 0..20 {
            assert_eq!(a.next_delay(), b.next_delay());
        }
    }

    #[test]
    fn attempts_count_up() {
        let mut b = bo();
        assert_eq!((1..=4).map(|_| b.next_delay().0).collect::<Vec<_>>(), vec![1, 2, 3, 4]);
    }

    #[test]
    fn reset_only_after_stable_session() {
        let mut b = bo();
        for _ in 0..5 {
            b.next_delay();
        }
        b.session_ended(Duration::from_secs(2)); // flapping session: no reset
        assert_eq!(b.attempt(), 5);
        b.session_ended(Duration::from_secs(59));
        assert_eq!(b.attempt(), 5);
        assert!(!b.take_reset());
        assert_eq!(b.next_delay().0, 6);
        b.session_ended(Duration::from_secs(60));
        assert_eq!(b.attempt(), 0);
        assert!(b.take_reset());
        assert!(!b.take_reset(), "reset flag is one-shot");
        assert_eq!(b.next_delay().0, 1);
    }

    #[test]
    fn jump_to_cap_waits_full_cap_and_stays_there() {
        let mut b = bo();
        assert_eq!(b.jump_to_cap(), (9, 300_000)); // 2s * 2^8 = 512s > cap at attempt 9
        assert_eq!(b.ceiling_ms(b.attempt()), 300_000);
        let (n, ms) = b.next_delay();
        assert_eq!(n, 10);
        assert!(ms >= 1_000 && ms <= 300_000);
        b.session_ended(Duration::from_secs(1)); // a quick refusal again: no reset
        assert_eq!(b.jump_to_cap(), (10, 300_000), "never moves back down");
    }

    #[test]
    fn env_overrides_and_sane_defaults() {
        assert_eq!(env_u64("SPIKE_REDIAL_TEST_UNSET_KEY", 7), 7);
        let b = Backoff::new(0, 0, Duration::ZERO, 0);
        assert!(b.floor_ms() >= 1 && b.ceiling_ms(1) >= 1);
        let mut small = Backoff::new(10, 40, Duration::from_millis(100), 1);
        assert!((1..20).all(|_| small.next_delay().1 <= 40));
    }
}
