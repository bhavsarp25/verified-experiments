"""A deliberately fraudulent module. It is NEVER imported or run. It exists only
so the reviewer (G9) can be tested against every fake pattern at once. If the
reviewer stops flagging these, the reviewer is broken."""
import numpy as np

accuracy = 0.87            # HARDCODED_METRIC: a made-up number posing as a result
test_acc = 0.991           # HARDCODED_METRIC


def load_eeg_data():
    return np.random.randn(288, 22, 500)   # SYNTHETIC_LOADER: fake data from a "loader"


def risky_step():
    try:
        compute_something()
    except Exception:
        pass                # SILENT_EXCEPT: swallow the error, pretend it worked


def load_with_fallback():
    try:
        return real_loader()
    except Exception:
        return np.zeros((288, 22, 500))     # FABRICATED_FALLBACK

# TODO: replace all of the above before anyone sees it
rng = np.random.default_rng()               # UNSEEDED_RNG
