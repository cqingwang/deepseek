"""Readiness means staged artifacts, never automatic boot or qualification."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kernel_readiness", ROOT / "scripts/kernel_readiness.py")
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)
POLICY = json.loads((ROOT / "config/kernel-trial.json").read_text())
STABLE = POLICY["stable"]


def menu(uuid="node-a", default=None):
    default_id = f"gnulinux-advanced-{uuid}>gnulinux-{POLICY['default']}-advanced-{uuid}"
    return f'''if [ "${{next_entry}}" ]; then
 set default="${{next_entry}}"
else
 set default="{default_id if default is None else default}"
fi
submenu 'Advanced options' $menuentry_id_option 'gnulinux-advanced-{uuid}' {{
 menuentry 'stable' $menuentry_id_option 'gnulinux-{STABLE}-advanced-{uuid}' {{
  linux /boot/vmlinuz-{STABLE} root=UUID={uuid}
 }}
}}
'''


def ready(uuid="node-a"):
    return {"kernel": STABLE, "page_size": 65536, "driver": POLICY["driver"],
            "loaded_nvidia_sources": {"nvidia": "stock-rm", "nvidia_uvm": POLICY['memory_saver']['srcversion']},
            "uvm_leaf_packing": "Y",
            "memory_saver_dkms": f"dgx-spark-memory-saver/{POLICY['memory_saver']['version']}, {STABLE}, aarch64: installed (Original modules exist)",
            "memory_saver_disk": {"srcversion": POLICY['memory_saver']['srcversion'],
                                  "filename": f"/lib/modules/{STABLE}/updates/dkms/nvidia-uvm.ko.zst", "signer": "Fleet key"},
            "thp": "always madvise [never]", "min_free_kbytes": "45166",
            "active_swap": ["/swap-64k.img"], "memory_service": "active",
            "memory_service_unit": "spark-ds41f-kernel-memory.service",
            "secure_boot": "SecureBoot enabled", "fan_signer": "Fleet key", "fan_key_enrolled": True,
            "errors": [], "packages": POLICY["packages"].copy(),
            "files": {f"/boot/{kind}-{STABLE}": True for kind in ("config", "vmlinuz", "initrd.img")},
            "boot_images": [STABLE],
            "headers": True, "config_64k": True,
            "cpu_tools": True, "governors": ["performance"],
            "modules": {m: {"vermagic": STABLE + " SMP", "version": POLICY["driver"]} for m in
                        ("nvidia", "nvidia_uvm", "nvidia_drm", "mlx5_core", "mlx5_ib", "dgx_ec_fan_control")},
            "fan_dkms": f"dgx-spark-fan-control/0.1.4, {STABLE}, aarch64: installed",
            "grub": kernel.grub_inventory(menu(uuid), "")}


class ReadinessTest(unittest.TestCase):
    def test_single_stable_kernel_is_success(self):
        self.assertEqual(kernel.findings("dgx1", ready(), POLICY), ([], []))

    def test_missing_package_is_not_installed(self):
        data = ready()
        package = next(iter(data["packages"]))
        data["packages"][package] = ""
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("package versions differ" in x for x in issues))
        self.assertIn(f"{package}={POLICY['packages'][package]}", fixes[0])

    def test_nvidia_modules_match_on_the_kernel_version(self):
        modules = next(p for p in POLICY["packages"] if p.startswith("linux-modules-nvidia-"))
        for suffix in ("+1", "+2", "+9"):
            data = ready()
            data["packages"][modules] = POLICY["packages"][modules] + suffix
            self.assertEqual(kernel.findings("dgx1", data, POLICY), ([], []))
        data = ready()
        data["packages"][modules] = "7.0.0-1020.20~24.04.1+1"
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("package versions differ" in x for x in issues))
        self.assertTrue(fixes[0].endswith(f"install {modules}"))

    def test_wrong_driver_and_vermagic(self):
        data = ready()
        data["modules"]["nvidia"] = {"version": "wrong", "vermagic": "7.0.0-1025-nvidia"}
        issues, _ = kernel.findings("dgx1", data, POLICY)
        self.assertEqual(sum("nvidia" in x for x in issues), 2)

    def test_missing_fan_and_initrd(self):
        data = ready()
        data["fan_dkms"] = "built"
        data["files"][f"/boot/initrd.img-{STABLE}"] = False
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("initrd" in x for x in issues))
        self.assertTrue(any("dkms autoinstall" in x for x in fixes))

    def test_numeric_default_is_not_a_safe_pin(self):
        data = ready()
        data["grub"] = kernel.grub_inventory(menu(default="0"), "")
        self.assertTrue(any("normal boot" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_one_shot_and_recovery_overrides(self):
        for env in ("next_entry=trial", "initrdfail=1\nprev_entry=trial"):
            with self.subTest(env=env):
                data = ready()
                data["grub"] = kernel.grub_inventory(menu(), env)
                self.assertTrue(any("override is armed" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_node_specific_grub_ids_do_not_cause_drift(self):
        self.assertEqual(kernel.alignment({"dgx1": ready("a"), "dgx2": ready("b")}), [])

    def test_second_kernel_and_page_size_are_reported(self):
        second = ready()
        second.update(kernel="7.0.0-1025-nvidia", page_size=4096,
                      boot_images=["7.0.0-1025-nvidia"])
        self.assertEqual(len(kernel.alignment({"dgx1": ready(), "dgx2": second})), 2)
        self.assertTrue(any("/boot must contain exactly" in x for x in
                            kernel.findings("dgx2", second, POLICY)[0]))

    def test_failed_probe_never_passes_alignment(self):
        issues = kernel.alignment({"dgx1": ready(), "dgx2": {"errors": ["SSH failed"]}})
        self.assertTrue(any("page_size" in x for x in issues))

    def test_same_driver_version_does_not_hide_different_loaded_uvm(self):
        second = ready()
        second["loaded_nvidia_sources"]["nvidia_uvm"] = "trial-uvm"
        second["uvm_leaf_packing"] = "N"
        issues = kernel.alignment({"dgx1": ready(), "dgx2": second})
        self.assertEqual(len(issues), 2)
        self.assertTrue(any("loaded_nvidia_sources" in x for x in issues))
        self.assertTrue(any("uvm_leaf_packing" in x for x in issues))

    def test_absent_module_is_reported(self):
        data = ready()
        del data["modules"]["mlx5_ib"]
        self.assertTrue(any("mlx5_ib" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_identically_wrong_live_driver_still_warns(self):
        data = ready()
        data["driver"] = "wrong"
        self.assertTrue(any("loaded driver" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_non_stable_page_size_is_reported(self):
        data = ready()
        data["page_size"] = 4096
        self.assertTrue(any("kernel/page size" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_unsigned_fan_is_not_ready_under_secure_boot(self):
        data = ready()
        data["fan_signer"] = ""
        self.assertTrue(any("stable fan signature" in x for x in kernel.findings("dgx1", data, POLICY)[0]))

    def test_mokutil_enrollment_exit_status_is_not_shell_success(self):
        self.assertTrue(kernel.key_enrolled(1, "/root/key.der is already enrolled\n"))
        self.assertFalse(kernel.key_enrolled(0, "/root/key.der is not enrolled"))
        self.assertFalse(kernel.key_enrolled(1, "Failed to read key"))

    def test_64k_boot_requires_its_swap_and_memory_policy(self):
        data = ready()
        data.update(active_swap=[], memory_service="", thp="always madvise [never]")
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("64 KiB swap" in x for x in issues))
        self.assertTrue(any("journalctl -u spark-ds41f-kernel-memory.service" in x for x in fixes))
        data.update(active_swap=["/swap-64k.img"], memory_service="active")
        self.assertEqual(kernel.findings("dgx1", data, POLICY), ([], []))

    def test_legacy_spark3_memory_service_is_the_same_policy(self):
        data = ready()
        data.update(thp="always madvise [never]",
                    active_swap=["/swap-64k.img"], memory_service="active",
                    memory_service_unit="spark3-kernel-memory.service")
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("still spark3-kernel-memory.service" in x for x in issues))
        self.assertFalse(any("is not active" in x for x in issues))
        self.assertTrue(any("journalctl -u spark3-kernel-memory.service" in x for x in fixes))

    def test_missing_memory_saver_warns_on_stable_kernel(self):
        data = ready()
        data["memory_saver_dkms"] = ""
        data["memory_saver_disk"]["srcversion"] = "stock"
        issues, fixes = kernel.findings("dgx1", data, POLICY)
        self.assertTrue(any("memory-saver DKMS" in x and "missing" in x for x in issues))
        self.assertTrue(any("dkms install" in x for x in fixes))
        profile = {"release": STABLE, "page_size": 65536, "memory_saver_required": False}
        self.assertEqual(kernel.profile_problems("dgx1", data, profile, POLICY), [])

    def test_large_profile_requires_stable_memory_saver_module(self):
        profile = {"release": STABLE, "page_size": 65536, "memory_saver_required": True}
        data = ready()
        data["memory_saver_dkms"] = ""
        data["memory_saver_disk"]["srcversion"] = "stock"
        data["loaded_nvidia_sources"]["nvidia_uvm"] = "stock-uvm"
        data["uvm_leaf_packing"] = "stock"
        self.assertTrue(kernel.profile_problems("dgx1", data, profile, POLICY))

    def test_disk_install_does_not_hide_unloaded_or_disabled_packing(self):
        profile = {"release": STABLE, "page_size": 65536, "memory_saver_required": True}
        data = ready()
        data.update(uvm_leaf_packing="N",
                    loaded_nvidia_sources={"nvidia": "stock-rm", "nvidia_uvm": "stock-uvm"})
        self.assertEqual(len(kernel.profile_problems("dgx1", data, profile, POLICY)), 2)
        data["loaded_nvidia_sources"]["nvidia_uvm"] = POLICY["memory_saver"]["srcversion"]
        data["uvm_leaf_packing"] = "N"
        self.assertTrue(kernel.profile_problems("dgx1", data, profile, POLICY))
        data["uvm_leaf_packing"] = "Y"
        self.assertEqual(kernel.profile_problems("dgx1", data, profile, POLICY), [])

    def test_wrong_dkms_version_or_unsigned_module_is_reported(self):
        data = ready()
        data["memory_saver_dkms"] = data["memory_saver_dkms"].replace(POLICY["memory_saver"]["version"], "0.1.0")
        data["memory_saver_disk"]["signer"] = ""
        self.assertEqual(len(kernel.memory_saver_problems("dgx1", data, POLICY)), 2)

    def test_stable_default_resolves_to_stable_kernel(self):
        data = ready()
        stable_id = next(k for k,v in data["grub"]["entries"].items() if v == STABLE)
        data["grub"] = kernel.grub_inventory(menu(default=stable_id), "")
        self.assertEqual(kernel.findings("dgx1", data, dict(POLICY, default=STABLE)), ([], []))


class RmQualificationTest(unittest.TestCase):
    def test_loaded_binary_identity_required_even_when_release_matches(self):
        policy = dict(POLICY, rm_build_note="candidate-build")
        profile = {"release": STABLE, "page_size": 65536}
        data = {"kernel": STABLE, "page_size": 65536, "driver": policy["driver"]}
        self.assertTrue(kernel.profile_problems("node", data, profile, policy))
        data["rm_build_note"] = "stock-build"
        self.assertTrue(kernel.profile_problems("node", data, profile, policy))
        data["rm_build_note"] = "candidate-build"
        self.assertEqual(kernel.profile_problems("node", data, profile, policy), [])

    def test_system_pool_policy_required(self):
        policy = dict(POLICY, rm_system_memory_pools="0")
        profile = {"release": STABLE, "page_size": 65536}
        data = {"kernel": STABLE, "page_size": 65536, "rm_system_memory_pools": "529"}
        self.assertTrue(kernel.profile_problems("node", data, profile, policy))
        data["rm_system_memory_pools"] = "0"
        self.assertEqual(kernel.profile_problems("node", data, profile, policy), [])


if __name__ == "__main__":
    unittest.main()
