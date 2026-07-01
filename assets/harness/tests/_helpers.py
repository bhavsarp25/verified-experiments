"""Synthetic EEG generators for testing the GUARDS themselves (never for
producing results). One looks like real EEG (correlated channels), one is
deliberately too-clean so the integrity guard rejects it."""
import numpy as np


def make_realistic_eeg(n_trials=200, n_ch=22, n_times=250, n_classes=4, seed=0):
    """Channels share latent sources -> real-EEG-like cross-channel correlation."""
    rng = np.random.default_rng(seed)
    latent = rng.standard_normal((n_trials, 3, n_times))
    mix = rng.standard_normal((n_ch, 3))
    X = np.einsum("ck,tkn->tcn", mix, latent)
    X += 0.1 * rng.standard_normal((n_trials, n_ch, n_times))
    y = rng.integers(0, n_classes, n_trials)
    return X.astype(np.float64), y.astype(int)


def make_iid_eeg(n_trials=200, n_ch=22, n_times=250, n_classes=4, seed=0):
    """i.i.d. noise: channels are ~uncorrelated -> the 'too clean' tell fires."""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n_trials, n_ch, n_times))
    y = rng.integers(0, n_classes, n_trials)
    return X.astype(np.float64), y.astype(int)
