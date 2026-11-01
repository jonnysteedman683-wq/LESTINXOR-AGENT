import pytest

from lestinxor.convergence import ConvergencePolicy, jaccard_distance


def test_convergence_policy_invalid_target_score():
    with pytest.raises(ValueError, match=r"target_score must be in \[0, 1\]"):
        ConvergencePolicy(target_score=1.5)
    with pytest.raises(ValueError, match=r"target_score must be in \[0, 1\]"):
        ConvergencePolicy(target_score=-0.5)


def test_convergence_policy_invalid_patience_max_generations():
    with pytest.raises(ValueError, match="patience and max_generations must be >= 1"):
        ConvergencePolicy(patience=0)
    with pytest.raises(ValueError, match="patience and max_generations must be >= 1"):
        ConvergencePolicy(max_generations=0)


def test_jaccard_distance_empty():
    assert jaccard_distance("", "  ") == 0.0


def test_jaccard_distance_nonempty():
    assert jaccard_distance("a b", "a c") > 0.0


def test_diversity_empty():
    from lestinxor.convergence import diversity

    assert diversity([]) == 1.0
    assert diversity(["a"]) == 1.0


def test_diversity_nonempty():
    from lestinxor.convergence import diversity

    assert diversity(["a b", "a c", "d e"]) > 0.0


def test_tracker_converged_target():
    from lestinxor.convergence import ConvergencePolicy, ConvergenceTracker

    tracker = ConvergenceTracker(ConvergencePolicy())
    assert tracker.observe(0, 0.96, ["a", "b"]) == "converged: target reached (0.96 >= 0.95)"


def test_tracker_converged_plateau():
    from lestinxor.convergence import ConvergencePolicy, ConvergenceTracker

    tracker = ConvergenceTracker(ConvergencePolicy(patience=1))
    assert tracker.observe(0, 0.5, ["a", "b"]) is None
    assert tracker.observe(1, 0.5, ["a", "b"]) == "converged: plateau (1 generations without +0.01)"


def test_tracker_converged_diversity():
    from lestinxor.convergence import ConvergencePolicy, ConvergenceTracker

    tracker = ConvergenceTracker(ConvergencePolicy())
    assert tracker.observe(0, 0.5, ["a", "a"]) == "converged: diversity collapse (0.00 < 0.15)"


def test_tracker_budget():
    from lestinxor.convergence import ConvergencePolicy, ConvergenceTracker

    tracker = ConvergenceTracker(ConvergencePolicy(max_generations=1))
    assert tracker.observe(0, 0.5, ["a", "b"]) == "budget: max generations (1)"
