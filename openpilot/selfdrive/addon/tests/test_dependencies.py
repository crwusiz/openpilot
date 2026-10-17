import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from openpilot.selfdrive.addon import dependencies as deps


class TestDependencySelection(unittest.TestCase):
  def test_cluster_combinations(self):
    for transport, expected in (
      ("usb", ("cluster-render", "cluster-usb")),
      ("network", ("cluster-h264",)),
      (None, ("cluster-h264",)),
    ):
      with self.subTest(transport=transport):
        self.assertEqual(deps.groups_for_cluster(transport), expected)

  def test_raw_params_defaults_and_block(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      self.assertEqual(deps.select_groups(root, blocked=""), ("dashboard",))
      (root / "ClusterEnable").write_text("1")
      self.assertEqual(deps.select_groups(root, blocked=""), ("dashboard", "cluster-h264"))

      self.assertEqual(deps.select_groups(root, blocked="dashboard"), ("cluster-h264",))
      (root / "ClusterDisplayTransport").write_text("usb")
      self.assertEqual(deps.select_groups(root, blocked=" dashboard , other "), ("cluster-render", "cluster-usb"))

      self.assertEqual(deps.select_groups(root, blocked="dashboard"), ("cluster-render", "cluster-usb"))
      self.assertEqual(deps.select_groups(root, blocked="cluster"), ("dashboard",))
      self.assertEqual(deps.select_groups(root, blocked="dashboard,cluster"), ())
      with patch.dict(os.environ, {"BLOCK": "dashboard,cluster"}):
        self.assertEqual(deps.select_groups(root), ())

  def test_invalid_transport_and_unknown_groups_fail_clearly(self):
    with self.assertRaisesRegex(deps.DependencyError, "Unsupported cluster"):
      deps.groups_for_cluster("typo")
    with self.assertRaisesRegex(deps.DependencyError, "Unknown addon"):
      deps.groups_ready(("typo",))

  def test_usb_group_checks_the_vendor_des_cipher(self):
    self.assertIn("Crypto.Cipher.DES", deps.GROUP_MODULES["cluster-usb"])
    _path, _content, pins = deps._requirements("cluster-usb")
    self.assertIn(("pycryptodome", "3.23.0"), pins)

  def test_cache_root_can_be_overridden(self):
    with patch.dict(os.environ, {"ADDON_DEPS_ROOT": "a-local-cache"}):
      self.assertEqual(deps.default_deps_root(), Path("a-local-cache"))


class TestDependencyPreparation(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.requirements = self.root / "requirements"
    self.requirements.mkdir()
    self.overlay = self.root / "packages"
    self.content = b"fake-package==1.2.3\n"
    for group in deps.GROUP_MODULES:
      (self.requirements / f"requirements-{group}.txt").write_bytes(self.content)
    self.patcher = patch.object(deps, "REQUIREMENTS_DIR", self.requirements)
    self.patcher.start()
    self.addCleanup(self.patcher.stop)

  def installed(self, group, content=None):
    target = self.overlay / group
    target.mkdir(parents=True, exist_ok=True)
    (target / deps.MARKER).write_text(deps._fingerprint(self.content if content is None else content))
    (target / "payload").write_text("old")
    return target

  @staticmethod
  def overlay_imports(_group, _pins, _root, overlay=None):
    return overlay is not None

  def test_system_exact_versions_and_imports_need_no_writable_cache(self):
    with patch.object(deps, "_probe", return_value=True), patch.object(deps, "_run_installer") as installer:
      self.assertTrue(deps.prepare_groups(("cluster-render",), deps_root=self.overlay))
      self.assertTrue(deps.groups_ready(("cluster-render",), deps_root=self.overlay))
    installer.assert_not_called()
    self.assertFalse(self.overlay.exists())

  def test_install_publishes_only_requested_groups(self):
    def install(_requirements, stage, _cancel):
      (stage / "payload").write_text("new")

    with patch.object(deps, "_probe", side_effect=self.overlay_imports), patch.object(deps, "_run_installer", side_effect=install) as installer:
      self.assertTrue(deps.prepare_groups(("cluster-render",), deps_root=self.overlay))
      self.assertTrue(deps.groups_ready(("cluster-render",), deps_root=self.overlay))
      deps.prepare_groups(("cluster-render",), deps_root=self.overlay)
    self.assertEqual(installer.call_count, 1)
    self.assertEqual((self.overlay / "cluster-render" / "payload").read_text(), "new")
    self.assertFalse((self.overlay / "cluster-usb").exists())
    self.assertFalse((self.overlay / "cluster-h264").exists())
    self.assertFalse(list(self.overlay.glob("*.staging-*")))

  def test_changed_requirements_and_python_abi_invalidate_marker(self):
    self.installed("cluster-render")
    with patch.object(deps, "_probe", side_effect=self.overlay_imports):
      self.assertTrue(deps.groups_ready(("cluster-render",), deps_root=self.overlay))
      (self.requirements / "requirements-cluster-render.txt").write_bytes(self.content + b"# changed\n")
      self.assertFalse(deps.groups_ready(("cluster-render",), deps_root=self.overlay))
      (self.requirements / "requirements-cluster-render.txt").write_bytes(self.content)
      with patch.object(deps, "_python_abi", return_value="other-python-abi"):
        self.assertFalse(deps.groups_ready(("cluster-render",), deps_root=self.overlay))

  def test_marker_does_not_hide_failed_native_import(self):
    self.installed("cluster-render")
    with patch.object(deps, "_probe", return_value=False):
      self.assertFalse(deps.groups_ready(("cluster-render",), deps_root=self.overlay))

  def test_onroad_preparation_never_calls_installer(self):
    with patch.object(deps, "_probe", return_value=False), patch.object(deps, "_run_installer") as installer:
      with self.assertRaisesRegex(deps.DependencyError, "while offroad"):
        deps.prepare_groups(("cluster-render",), deps_root=self.overlay, allow_install=False)
    installer.assert_not_called()
    self.assertFalse(self.overlay.exists())

  def test_installer_failure_keeps_previous_group_and_cleans_stage(self):
    target = self.installed("cluster-render", b"old-requirement==1\n")
    with patch.object(deps, "_probe", return_value=False), patch.object(deps, "_run_installer", side_effect=deps.DependencyError("offline")):
      with self.assertRaisesRegex(deps.DependencyError, "offline"):
        deps.prepare_groups(("cluster-render",), deps_root=self.overlay)
    self.assertEqual((target / "payload").read_text(), "old")
    self.assertFalse(list(self.overlay.glob("*.staging-*")))

  def test_failed_stage_probe_preserves_old_group(self):
    target = self.installed("cluster-usb", b"old-requirement==1\n")
    with patch.object(deps, "_probe", return_value=False), patch.object(deps, "_run_installer"):
      with self.assertRaisesRegex(deps.DependencyError, "version/import check"):
        deps.prepare_groups(("cluster-usb",), deps_root=self.overlay)
    self.assertEqual((target / "payload").read_text(), "old")

  def test_atomic_publication_failure_restores_previous_group(self):
    target = self.installed("cluster-render")
    stage = self.overlay / ".staging"
    stage.mkdir()
    (stage / "payload").write_text("new")
    original_replace = Path.replace

    def replace(path, destination):
      if path == stage:
        raise OSError("simulated rename failure")
      return original_replace(path, destination)

    with patch.object(Path, "replace", replace), self.assertRaisesRegex(OSError, "simulated"):
      deps._publish(stage, target)
    self.assertEqual((target / "payload").read_text(), "old")
    self.assertFalse((self.overlay / ".cluster-render.previous").exists())

  def test_activation_exposes_only_selected_groups(self):
    render = self.installed("cluster-render")
    usb = self.installed("cluster-usb")
    original = list(sys.path)
    self.addCleanup(lambda: sys.path.__setitem__(slice(None), original))
    sys.path.insert(0, str(usb))
    with patch.object(deps, "_probe", side_effect=self.overlay_imports):
      deps.activate_groups(("cluster-render",), deps_root=self.overlay)
    self.assertEqual(sys.path[0], str(render))
    self.assertNotIn(str(usb), sys.path)

  def test_cli_check_does_not_install_and_install_errors_are_reported(self):
    params = self.root / "params"
    params.mkdir()
    arguments = ["selected", "--params-root", str(params), "--deps-root", str(self.overlay)]
    with patch.object(deps, "_probe", return_value=False), patch.object(deps, "_run_installer") as installer:
      self.assertEqual(deps.main([*arguments, "--check"]), 1)
    installer.assert_not_called()
    with patch.object(deps, "prepare_groups", side_effect=deps.DependencyError("offline")), patch("sys.stderr", new_callable=io.StringIO) as errors:
      self.assertEqual(deps.main(arguments), 1)
      self.assertIn("offline", errors.getvalue())

  def test_probe_checks_exact_metadata_and_real_imports_in_fresh_process(self):
    package = self.root / "probe-overlay"
    package.mkdir()
    (package / "addon_probe_demo.py").write_text("value = 1\n")
    metadata = package / "addon_probe_demo-1.2.3.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: addon-probe-demo\nVersion: 1.2.3\n")
    with patch.dict(deps.GROUP_MODULES, {"cluster-usb": ("addon_probe_demo",)}):
      self.assertTrue(deps._probe("cluster-usb", [("addon-probe-demo", "1.2.3")], self.overlay, package))
      self.assertFalse(deps._probe("cluster-usb", [("addon-probe-demo", "9.9.9")], self.overlay, package))
      (package / "addon_probe_demo.py").write_text("raise ImportError('native dependency unavailable')\n")
      self.assertFalse(deps._probe("cluster-usb", [("addon-probe-demo", "1.2.3")], self.overlay, package))
    self.assertNotIn("addon_probe_demo", sys.modules)

  def test_per_group_lock_serializes_publishers(self):
    entered = threading.Event()
    started = threading.Event()
    errors = []

    def contender():
      started.set()
      try:
        with deps._group_lock(self.overlay, "cluster-render"):
          entered.set()
      except BaseException as error:
        errors.append(error)

    with deps._group_lock(self.overlay, "cluster-render"):
      worker = threading.Thread(target=contender)
      worker.start()
      self.assertTrue(started.wait(timeout=2))
      self.assertFalse(entered.wait(timeout=0.15))
    worker.join(timeout=3)
    self.assertFalse(worker.is_alive())
    self.assertFalse(errors)
    self.assertTrue(entered.is_set())


class TestInstallerLifecycle(unittest.TestCase):
  def test_pip_command_is_bounded_and_uses_only_wheels(self):
    with patch.object(deps.subprocess, "run", return_value=Mock(returncode=0)):
      command = deps._installer_command(Path("requirements.txt"), Path("staging"))
    self.assertEqual(command[:4], [sys.executable, "-m", "pip", "install"])
    self.assertIn("--only-binary=:all:", command)
    self.assertIn("--no-cache-dir", command)
    self.assertEqual(command[command.index("--timeout") + 1], "15")
    self.assertEqual(command[command.index("--retries") + 1], "1")

  def test_uv_fallback_uses_same_python(self):
    with patch.object(deps.subprocess, "run", return_value=Mock(returncode=1)), patch.object(deps.shutil, "which", return_value="uv"):
      command = deps._installer_command(Path("requirements.txt"), Path("staging"))
    self.assertEqual(command[:5], ["uv", "pip", "install", "--python", sys.executable])

  def test_transition_to_onroad_stops_pip_child(self):
    process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired("pip", 1)
    with patch.object(deps, "_installer_command", return_value=["pip"]), patch.object(deps.subprocess, "Popen", return_value=process), \
         patch.object(deps, "_stop_installer") as stop, patch.object(deps.signal, "signal", return_value=deps.signal.SIG_DFL):
      with self.assertRaises(deps.DependencyInstallCancelled):
        deps._run_installer(Path("requirements.txt"), Path("staging"), Mock(side_effect=[False, False, False, True]))
    stop.assert_called_once_with(process)

  def test_timeout_stops_pip_child(self):
    process = Mock()
    with patch.object(deps, "_installer_command", return_value=["pip"]), patch.object(deps.subprocess, "Popen", return_value=process), \
         patch.object(deps, "_stop_installer") as stop, patch.object(deps.signal, "signal", return_value=deps.signal.SIG_DFL), \
         patch.object(deps.time, "monotonic", side_effect=[0, deps.INSTALL_TIMEOUT + 1]):
      with self.assertRaisesRegex(deps.DependencyError, "exceeded"):
        deps._run_installer(Path("requirements.txt"), Path("staging"))
    stop.assert_called_once_with(process)

  def test_sigterm_interrupt_stops_pip_child(self):
    process = Mock()
    handlers = []

    def install_handler(_signum, handler):
      handlers.append(handler)
      return deps.signal.SIG_DFL

    def interrupt(**_kwargs):
      handlers[0](deps.signal.SIGTERM, None)

    process.wait.side_effect = interrupt
    with patch.object(deps, "_installer_command", return_value=["pip"]), patch.object(deps.subprocess, "Popen", return_value=process), \
         patch.object(deps, "_stop_installer") as stop, patch.object(deps.signal, "signal", side_effect=install_handler):
      with self.assertRaises(SystemExit) as interrupted:
        deps._run_installer(Path("requirements.txt"), Path("staging"))
    stop.assert_called_once_with(process)
    self.assertEqual(interrupted.exception.code, 128 + deps.signal.SIGTERM)
    self.assertEqual(handlers[-1], deps.signal.SIG_DFL)


if __name__ == "__main__":
  unittest.main()
