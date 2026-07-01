"""G8: every guard must both PASS real inputs and REJECT a deliberate fake."""
import os

import numpy as np
import pytest

from _helpers import make_realistic_eeg, make_iid_eeg

from provenance.manifest import build_manifest, sha256_array, Manifest
from guards import (
    DataIntegrityError, LeakageError, SanityError, AuditError,
    ReproducibilityError, FabricationError,
)
from guards.data_integrity import assert_real_eeg, EEGSpec, assert_real_data
from guards.leakage import label_shuffle_invariance
from guards.sanity import (
    assert_not_above_oracle, assert_valid_accuracy, beats_chance, random_label_control,
    chance_level,
)
from guards.audit import recompute_accuracy, assert_report_matches_predictions
from guards.reproducibility import assert_reproducible
from guards.fabrication import fail_loud, forbid_synthetic_fallback, Result


ACC = lambda yt, yp: float(np.mean(np.asarray(yt) == np.asarray(yp)))


# ---- G1 provenance ---------------------------------------------------------
def test_manifest_is_grounded_only_with_real_data():
    X, _ = make_realistic_eeg()
    m = build_manifest(X, seed=0)
    assert m.is_grounded
    assert len(m.data_sha256) == 64
    assert m.n_samples == X.shape[0]

def test_hash_changes_with_data():
    X, _ = make_realistic_eeg(seed=1)
    h1 = sha256_array(X)
    X2 = X.copy(); X2[0, 0, 0] += 1.0
    assert sha256_array(X2) != h1

def test_fabricated_manifest_is_not_grounded():
    fake = Manifest("deadbeef", 0, "NO_GIT", 0, "3", "2", "p", "t")
    assert not fake.is_grounded


# ---- G7 fabrication --------------------------------------------------------
def test_fail_loud_raises():
    with pytest.raises(FabricationError):
        fail_loud(False, "nope")

def test_no_synthetic_fallback_on_loader_failure():
    def broken_loader():
        raise IOError("disk gone")
    with pytest.raises(FabricationError):
        forbid_synthetic_fallback(broken_loader)

def test_result_requires_real_predictions_and_manifest(tmp_path):
    X, y = make_realistic_eeg()
    preds = tmp_path / "p.csv"
    preds.write_text("y_true,y_pred\n0,0\n1,1\n")
    m = build_manifest(X, seed=0)
    ok = Result("adaptive", 0.6, m, str(preds))   # real manifest + real file
    assert ok.value == 0.6

def test_result_rejects_hardcoded_number_without_provenance():
    bad = Manifest("x", 0, "", 0, "3", "2", "p", "t")   # ungrounded
    with pytest.raises(FabricationError):
        Result("fake", 0.99, bad, "/does/not/exist.csv")


# ---- G2 data integrity -----------------------------------------------------
def test_real_like_eeg_passes():
    X, y = make_realistic_eeg()
    assert_real_eeg(X, y)          # must not raise

def test_constant_channel_rejected():
    X, y = make_realistic_eeg()
    X[:, 3, :] = 0.0               # kill a channel
    with pytest.raises(DataIntegrityError):
        assert_real_eeg(X, y)

def test_too_clean_iid_rejected():
    X, y = make_iid_eeg()
    with pytest.raises(DataIntegrityError):
        assert_real_eeg(X, y)

def test_wrong_channel_count_rejected():
    X, y = make_realistic_eeg(n_ch=8)
    with pytest.raises(DataIntegrityError):
        assert_real_eeg(X, y, EEGSpec(n_channels=22))

def test_generic_assert_real_data_passes_real_table():
    rng = np.random.default_rng(0)
    base = rng.standard_normal((200, 1))
    X = base + rng.standard_normal((200, 5)) * 0.1   # correlated columns
    assert_real_data(X, expected_ndim=2, min_rows=100, require_correlation=True)

def test_generic_assert_real_data_rejects_constant_and_nan():
    X = np.ones((150, 4))
    with pytest.raises(DataIntegrityError):
        assert_real_data(X)                          # constant columns
    Xn = np.random.default_rng(0).standard_normal((150, 4)); Xn[0, 0] = np.nan
    with pytest.raises(DataIntegrityError):
        assert_real_data(Xn)                         # NaN


# ---- G3 leakage ------------------------------------------------------------
def test_clean_adaptation_passes_leakage_check():
    X, y = make_realistic_eeg()
    def clean_adapt(X, y_like):    # ignores labels entirely
        return np.zeros(len(y_like), dtype=int)
    label_shuffle_invariance(clean_adapt, X, y, ACC)   # must not raise

def test_label_peeking_is_caught():
    X, y = make_realistic_eeg()
    def leaky_adapt(X, y_like):    # cheats: returns the labels it was given
        return np.asarray(y_like)
    with pytest.raises(LeakageError):
        label_shuffle_invariance(leaky_adapt, X, y, ACC)


# ---- G4 sanity -------------------------------------------------------------
def test_accuracy_above_oracle_rejected():
    with pytest.raises(SanityError):
        assert_not_above_oracle(0.92, oracle=0.70)

def test_accuracy_below_oracle_ok():
    assert_not_above_oracle(0.62, oracle=0.70)

def test_invalid_accuracy_rejected():
    with pytest.raises(SanityError):
        assert_valid_accuracy(1.4)

def test_beats_chance_math():
    assert beats_chance(0.60, n_classes=4, n_samples=288)
    assert not beats_chance(chance_level(4), n_classes=4, n_samples=288)

def test_negative_control_catches_leakage():
    X, y = make_realistic_eeg()
    def honest_fit(X, y_train):    # can't learn from shuffled labels
        return chance_level(4)
    random_label_control(honest_fit, X, y, n_classes=4)   # ok

    def leaky_fit(X, y_train):     # magically high even on shuffled labels
        return 0.95
    with pytest.raises(SanityError):
        random_label_control(leaky_fit, X, y, n_classes=4)


# ---- G5 audit --------------------------------------------------------------
def test_audit_matches_stored_predictions(tmp_path):
    p = tmp_path / "preds.csv"
    p.write_text("y_true,y_pred\n0,0\n1,1\n2,2\n3,0\n")   # 3/4 correct
    audited = recompute_accuracy(str(p))
    assert audited == pytest.approx(0.75)
    assert_report_matches_predictions(str(p), 0.75)       # ok

def test_audit_catches_inflated_report(tmp_path):
    p = tmp_path / "preds.csv"
    p.write_text("y_true,y_pred\n0,0\n1,0\n2,0\n3,0\n")   # 1/4 correct
    with pytest.raises(AuditError):
        assert_report_matches_predictions(str(p), 0.90)   # lied: claims 0.90


# ---- G6 reproducibility ----------------------------------------------------
def test_deterministic_run_passes():
    def det_run(seed):
        return np.random.default_rng(seed).standard_normal(5)
    assert_reproducible(det_run, seed=7)                  # must not raise

def test_nondeterministic_run_is_caught():
    def nondet_run(seed):
        return np.random.default_rng().standard_normal(5)  # ignores seed
    with pytest.raises(ReproducibilityError):
        assert_reproducible(nondet_run, seed=7)
