"""Trajectories must land somewhere findable.

save_trajectory() used to open a RELATIVE filename, so the file landed in whatever
directory the process started in. The CLI starts in your project; the gateway and the cron
scheduler start wherever launchd put them. Training data scattered across the disk — or
dropped when the cwd was read-only — is not training data.
"""
import json
import os

from agent.trajectory import save_trajectory, trajectory_dir

TRAJ = [{"from": "human", "value": "hi"}, {"from": "gpt", "value": "hello"}]


def test_dir_defaults_under_hermes_home(monkeypatch):
    monkeypatch.delenv("HERMES_TRAJECTORY_DIR", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/tmp/hh")
    assert trajectory_dir() == "/tmp/hh/trajectories"


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TRAJECTORY_DIR", str(tmp_path))
    assert trajectory_dir() == str(tmp_path)


def test_write_lands_in_the_dir_not_the_cwd(monkeypatch, tmp_path):
    """The regression itself: run from an unrelated cwd, find the file anyway."""
    dest = tmp_path / "traj"
    elsewhere = tmp_path / "some-project"
    elsewhere.mkdir()
    monkeypatch.setenv("HERMES_TRAJECTORY_DIR", str(dest))
    monkeypatch.chdir(elsewhere)

    save_trajectory(TRAJ, "minimax/m3", completed=True)

    written = dest / "trajectory_samples.jsonl"
    assert written.exists(), "trajectory did not land in the declared directory"
    assert not (elsewhere / "trajectory_samples.jsonl").exists(), "still writing to cwd"
    entry = json.loads(written.read_text().splitlines()[0])
    assert entry["conversations"] == TRAJ
    assert entry["model"] == "minimax/m3"
    assert entry["completed"] is True


def test_failed_runs_go_to_their_own_file(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_TRAJECTORY_DIR", str(tmp_path))
    save_trajectory(TRAJ, "minimax/m3", completed=False)
    assert (tmp_path / "failed_trajectories.jsonl").exists()
    assert not (tmp_path / "trajectory_samples.jsonl").exists()


def test_explicit_filename_is_left_alone(monkeypatch, tmp_path):
    """batch_runner names its own per-batch output file; do not relocate it."""
    monkeypatch.setenv("HERMES_TRAJECTORY_DIR", str(tmp_path / "ignored"))
    target = tmp_path / "batch_001_output.jsonl"
    save_trajectory(TRAJ, "minimax/m3", completed=True, filename=str(target))
    assert target.exists()
    assert not (tmp_path / "ignored").exists()
