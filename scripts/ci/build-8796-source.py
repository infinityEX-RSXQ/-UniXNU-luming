"""Compile the reviewed 8796 source on macOS CI, without donor prelink or images.

The real build tree lives in RUNNER_TEMP, outside the device repository. Local
--check only validates inputs and adapts text in memory; it never copies sources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys

DEVICE = Path(__file__).resolve().parents[2]
BASE_COMMIT = '75bc13dd34dc4e403826793739e316a264ce00dd'
XNU_COMMIT = '1b191cb58250d0705d8a51287127505aa4bc0789'
BASELINE_HASH = '3486867d8c639eb10d31be16e875dbbdbb1d27afd97aedbb8ba930f0093c6852'
FIREHOSE_HASH = 'e30d44b2633fb509fefbb316cb2824abb394e3e5779d4b6d86f8ed2b4f91c057'
GENI_PATCH_NAME = '0001-pexpert-arm-add-polled-QCOM-GENI-UART-console.patch'
GENI_PATCH_HASH = '699b1bcfec619118bb378f3b8f69508a6f27b34fefb38fd45926cfc8ff5d8e07'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_exact(text, before, after, count):
    if text.count(before) != count:
        raise ValueError(f'Unexpected upstream context count for {before!r}')
    return text.replace(before, after)


def capture_kernel_make(script):
    """Record the expanded argv while executing the exact same real make."""
    start = script.index('\nmake -C .cache/xnu-work "${XNU_BUILD_TARGET:-build}"') + 1
    end_marker = 'DSTROOT="$ROOT/build/xnu/ios-dst"'
    end = script.index(end_marker, start) + len(end_marker)
    command = script[start:end]
    if command.count('\nmake ') or script.count(end_marker) != 1:
        raise ValueError('Ambiguous final kernel make command')
    captured = 'NUWA_KERNEL_MAKE=(' + command + ')\n'
    captured += ('python3 -c \'import json,sys; from pathlib import Path; '
                 'Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:], indent=2)+"\\n")\' '
                 '"$ROOT/reports/kernel-make-command.json" "${NUWA_KERNEL_MAKE[@]}"\n')
    captured += '"${NUWA_KERNEL_MAKE[@]}"'
    injection = r"""
python3 - <<'PY'
import re, pathlib
p = pathlib.Path('.cache/xnu-work/osfmk/arm64/start.s')
t = p.read_text(encoding='utf-8', errors='replace')
t = t.replace('\r\n', '\n').replace('\r', '\n')
a = 'LEXT(start_first_cpu)\n\n\t// Unlock the core for debugging'
if t.count(a) != 1:
    i = t.find('LEXT(start_first_cpu)')
    print('ENTRY-ANCHOR-MISSING count', t.count(a), 'at', i)
    raise SystemExit('entry anchor missing')
t = t.replace(a, ('LEXT(start_first_cpu)\n\n'
    '\t/* luming: UEFI/DarwinBoot may hand off with MMU enabled; start.s assumes MMU is off */\n'
    '\tmov\t\tx15, x0\n'
    '\tmrs\t\tx16, SCTLR_EL1\n'
    '\tmov\t\tx17, #1\n'
    '\tbic\t\tx16, x16, x17\n'
    '\tmsr\t\tSCTLR_EL1, x16\n'
    '\tisb\t\tsy\n'
    '\tmov\t\tx0, x15\n\n'
    '\t// Unlock the core for debugging'), 1)
print('ENTRY-FIX-OK')
b = re.search(r'(?m)^#if[^\n]*\n(?=\s*// Setup timer interrupt routing)', t)
if b:
    t = t[:b.start()] + '#if defined(BCM2837) && defined(APPLE_ARM64_ARCH_FAMILY)\n' + t[b.end():]
    print('BCM-GUARD-OK')
else:
    i = t.find('// Setup timer interrupt routing')
    if i >= 0:
        j = t.index('#endif', i)
        t = t[:i] + '#if defined(APPLE_ARM64_ARCH_FAMILY)  /* luming: no BCM2837 timer on UEFI */\n' + t[i:j] + '#endif' + t[j + 6:]
        print('BCM-BLOCK-GUARDED-BY-WRAP')
    else:
        print('BCM-TIMER-BLOCK-NOT-FOUND')
c = re.search(r'#if !VMAPPLE\n(\tMOV64   x1, SCTLR_EL1_DEFAULT\n\tcmp\t\tx0, x1\n\tbne\t\t\.\n)#endif /\* !VMAPPLE \*/', t)
if c:
    t = t[:c.start()] + '#if !VMAPPLE && defined(APPLE_ARM64_ARCH_FAMILY)\n' + c.group(1) + '#endif /* !VMAPPLE && APPLE_ARM64_ARCH_FAMILY */' + t[c.end():]
    print('SCTLR-GUARD-OK')
else:
    print('SCTLR-ANCHOR-MISSING')
p.write_text(t)
print('LUMING-START-FIX-DONE')
# ---- CD: PE clock defaults when Apple soc-base is absent (UEFI/DarwinBoot) ----
base = pathlib.Path('.cache/xnu-work')
pp = base / 'pexpert/arm/pe_identify_machine.c'
t2 = pp.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
old2 = '\tif (pe_arm_get_soc_base_phys() == 0) {\n\t\treturn;\n\t}\n'
if t2.count(old2) != 1:
    print('CD-PE-ANCHOR-MISSING', t2.count(old2))
    raise SystemExit('pe_identify_machine anchor missing')
new2 = ('\tif (pe_arm_get_soc_base_phys() == 0) {\n'
        '\t\t/* luming: no Apple soc-base under UEFI boot; keep clock defaults and read /cpus timebase */\n'
        '\t\tbzero((void *)&gPEClockFrequencyInfo, sizeof(clock_frequency_info_t));\n'
        '\t\tgPEClockFrequencyInfo.timebase_frequency_hz = 24000000;\n'
        '\t\tgPEClockFrequencyInfo.bus_clock_rate_hz = 100000000;\n'
        '\t\tgPEClockFrequencyInfo.cpu_clock_rate_hz = 400000000;\n'
        '\t\tDTEntry cpus_l = NULL, cpu_l = NULL;\n'
        '\t\tOpaqueDTEntryIterator iter_l;\n'
        '\t\tif (SecureDTLookupEntry(NULL, "/cpus", &cpus_l) == kSuccess &&\n'
        '\t\t    SecureDTInitEntryIterator(cpus_l, &iter_l) == kSuccess) {\n'
        '\t\t\twhile (SecureDTIterateEntries(&iter_l, &cpu_l) == kSuccess) {\n'
        '\t\t\t\tvoid const *value_l = NULL;\n'
        '\t\t\t\tunsigned int size_l = 0;\n'
        '\t\t\t\tif (SecureDTGetProperty(cpu_l, "timebase-frequency", &value_l, &size_l) == kSuccess && value_l) {\n'
        '\t\t\t\t\tuint64_t tb_l = (size_l == 8) ? *(uint64_t const *)value_l : *(uint32_t const *)value_l;\n'
        '\t\t\t\t\tif (tb_l != 0) {\n'
        '\t\t\t\t\t\tgPEClockFrequencyInfo.timebase_frequency_hz = tb_l;\n'
        '\t\t\t\t\t}\n'
        '\t\t\t\t\tbreak;\n'
        '\t\t\t\t}\n'
        '\t\t\t}\n'
        '\t\t}\n'
        '\t\tgPEClockFrequencyInfo.dec_clock_rate_hz = gPEClockFrequencyInfo.timebase_frequency_hz;\n'
        '\t\treturn;\n'
        '\t}\n')
t2 = t2.replace(old2, new2, 1)
pp.write_text(t2)
print('CD-PE-CLOCK-DEFAULTS-OK')
# ---- CD: guard wfe_timeout_configure when timebase/ticks are zero ----
pm = base / 'osfmk/arm64/machine_routines.c'
t3 = pm.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
old3 = '\tticks_per_sec = gPEClockFrequencyInfo.timebase_frequency_hz;\n\tticks_per_event = ticks_per_sec / events_per_sec;\n'
if t3.count(old3) != 1:
    print('CD-WFE-ANCHOR-MISSING', t3.count(old3))
    raise SystemExit('wfe_timeout_configure anchor missing')
new3 = ('\tticks_per_sec = gPEClockFrequencyInfo.timebase_frequency_hz;\n'
        '\tif (ticks_per_sec == 0) {\n'
        '\t\t/* luming: timebase unknown; disable WFE event stream instead of UBSAN underflow */\n'
        '\t\tarm64_eventi = 0;\n'
        '\t\treturn;\n'
        '\t}\n'
        '\tticks_per_event = ticks_per_sec / events_per_sec;\n'
        '\tif (ticks_per_event == 0) {\n'
        '\t\tarm64_eventi = 0;\n'
        '\t\treturn;\n'
        '\t}\n')
t3 = t3.replace(old3, new3, 1)
pm.write_text(t3)
print('CD-WFE-GUARD-OK')
# ---- CF: PE_init_platform identity defaults + skip optional iBoot DT blocks (luming) ----
ppi = base / 'pexpert/arm/pe_init.c'
t4 = ppi.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
old4 = '\tboot_args      *boot_args_ptr = (boot_args *) args;\n\n\tif (PE_state.initialized == FALSE) {\n'
if t4.count(old4) != 1:
    print('CF-PE-INIT-ANCHOR-MISSING', t4.count(old4))
    raise SystemExit('PE_init_platform anchor missing')
cf_defaults = ('\t/* luming: default PE identity for the minimal UEFI/DarwinBoot device tree */\n'
               '\tstrlcpy(gTargetTypeBuffer, "luming", sizeof(gTargetTypeBuffer));\n'
               '\tstrlcpy(gModelTypeBuffer, "Xiaomi Civi 5 Pro", sizeof(gModelTypeBuffer));\n'
               '\tstrlcpy(iBoot_version, "UEFI-DarwinBoot", sizeof(iBoot_version));\n'
               '\tmemset(gPlatformECID, 0, sizeof(gPlatformECID));\n'
               '\tgPlatformMemoryID = 0;\n')
t4 = t4.replace(old4, old4.replace('\n\tif (PE_state.initialized == FALSE) {\n', '\n' + cf_defaults + '\n\tif (PE_state.initialized == FALSE) {\n'), 1)
old5 = '\tif (!vm_initialized) {\n\t\tif (kSuccess == (SecureDTFindEntry("name", "device-tree", &entry))) {\n'
if t4.count(old5) != 1:
    print('CF-DTBLOCK-ANCHOR-MISSING', t4.count(old5))
    raise SystemExit('PE_init_platform DT block anchor missing')
t4 = t4.replace(old5, '\tif (!vm_initialized) {\n#if defined(APPLE_ARM64_ARCH_FAMILY)\n\t\tif (kSuccess == (SecureDTFindEntry("name", "device-tree", &entry))) {\n', 1)
old6 = '\t\t\t\tbcopy(prop, &gPlatformMemoryID, size);\n\t\t\t}\n\t\t}\n\t\tpe_init_debug();\n'
if t4.count(old6) != 1:
    print('CF-CHOSEN-END-ANCHOR-MISSING', t4.count(old6))
    raise SystemExit('PE_init_platform /chosen end anchor missing')
t4 = t4.replace(old6, '\t\t\t\tbcopy(prop, &gPlatformMemoryID, size);\n\t\t\t}\n\t\t}\n#endif /* APPLE_ARM64_ARCH_FAMILY: skip optional iBoot identity DT property blocks on luming */\n\t\tpe_init_debug();\n', 1)
ppi.write_text(t4)
print('CF-PE-INIT-DEFAULTS-OK')
print('CF-OPTIONAL-DT-BLOCKS-SKIPPED-OK')
# ---- CG: skip arm_init DEBUG DT blocks on luming (minimal UEFI DT) ----
pai = base / 'osfmk/arm/arm_init.c'
t7 = pai.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
g1_old = '#if (DEVELOPMENT || DEBUG)\n\tunsigned long const *platform_stall_ptr = NULL;'
if t7.count(g1_old) != 1:
    print('CG-STALL-BLOCK-ANCHOR-MISSING', t7.count(g1_old))
    raise SystemExit('arm_init DEBUG stall block anchor missing')
t7 = t7.replace(g1_old, '#if (DEVELOPMENT || DEBUG) && defined(APPLE_ARM64_ARCH_FAMILY)\n\tunsigned long const *platform_stall_ptr = NULL;', 1)
g2a_old = '\tunsigned long const *dram_base;\n\tunsigned long const *dram_size;\n\n\tif (SecureDTLookupEntry(NULL, "/chosen", &chosen) != kSuccess) {\n'
if t7.count(g2a_old) != 1:
    print('CG-DRAM-BLOCK-ANCHOR-MISSING', t7.count(g2a_old))
    raise SystemExit('arm_init dram DT block anchor missing')
t7 = t7.replace(g2a_old, '\tunsigned long const *dram_base;\n\tunsigned long const *dram_size;\n\n#if defined(APPLE_ARM64_ARCH_FAMILY)\n\tif (SecureDTLookupEntry(NULL, "/chosen", &chosen) != kSuccess) {\n', 1)
g2b_old = '\tgDramBase = *dram_base;\n\tgDramSize = *dram_size;\n'
if t7.count(g2b_old) != 1:
    print('CG-DRAM-END-ANCHOR-MISSING', t7.count(g2b_old))
    raise SystemExit('arm_init dram end anchor missing')
t7 = t7.replace(g2b_old, ('\tgDramBase = *dram_base;\n\tgDramSize = *dram_size;\n'
    '#else /* luming: minimal UEFI DT - DRAM range from boot args, skip /chosen lookups */\n'
    '\t(void)dram_base;\n\t(void)dram_size;\n'
    '\tgDramBase = (vm_offset_t)args->physBase;\n'
    '\tgDramSize = (vm_offset_t)args->memSize;\n#endif\n'), 1)
pai.write_text(t7)
print('CG-ARM-INIT-DEBUG-DT-SKIPPED-OK')
print('CG-DRAM-FROM-BOOTARGS-OK')
# ---- CH: remove all SecureDT calls from the luming early PE path ----
# CH1: pe_identify_machine no-soc-base branch: defaults only (no /cpus DT lookups)
pim2 = base / 'pexpert/arm/pe_identify_machine.c'
t8 = pim2.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
ch1_old = (chr(9) + 'if (pe_arm_get_soc_base_phys() == 0) {\n'
    + chr(9)*2 + '/* luming: no Apple soc-base under UEFI boot; keep clock defaults and read /cpus timebase */\n'
    + chr(9)*2 + 'bzero((void *)&gPEClockFrequencyInfo, sizeof(clock_frequency_info_t));\n'
    + chr(9)*2 + 'gPEClockFrequencyInfo.timebase_frequency_hz = 24000000;\n'
    + chr(9)*2 + 'gPEClockFrequencyInfo.bus_clock_rate_hz = 100000000;\n'
    + chr(9)*2 + 'gPEClockFrequencyInfo.cpu_clock_rate_hz = 400000000;\n'
    + chr(9)*2 + 'DTEntry cpus_l = NULL, cpu_l = NULL;\n'
    + chr(9)*2 + 'OpaqueDTEntryIterator iter_l;\n'
    + chr(9)*2 + 'if (SecureDTLookupEntry(NULL, "/cpus", &cpus_l) == kSuccess &&\n'
    + chr(9)*2 + '    SecureDTInitEntryIterator(cpus_l, &iter_l) == kSuccess) {\n'
    + chr(9)*3 + 'while (SecureDTIterateEntries(&iter_l, &cpu_l) == kSuccess) {\n'
    + chr(9)*4 + 'void const *value_l = NULL;\n'
    + chr(9)*4 + 'unsigned int size_l = 0;\n'
    + chr(9)*4 + 'if (SecureDTGetProperty(cpu_l, "timebase-frequency", &value_l, &size_l) == kSuccess && value_l) {\n'
    + chr(9)*5 + 'uint64_t tb_l = (size_l == 8) ? *(uint64_t const *)value_l : *(uint32_t const *)value_l;\n'
    + chr(9)*5 + 'if (tb_l != 0) {\n'
    + chr(9)*6 + 'gPEClockFrequencyInfo.timebase_frequency_hz = tb_l;\n'
    + chr(9)*5 + '}\n'
    + chr(9)*5 + 'break;\n'
    + chr(9)*4 + '}\n'
    + chr(9)*3 + '}\n'
    + chr(9)*2 + '}\n'
    + chr(9)*2 + 'gPEClockFrequencyInfo.dec_clock_rate_hz = gPEClockFrequencyInfo.timebase_frequency_hz;\n'
    + chr(9)*2 + 'return;\n'
    + chr(9) + '}\n')
if t8.count(ch1_old) != 1:
    print('CH1-ANCHOR-MISSING', t8.count(ch1_old))
    raise SystemExit('pe_identify_machine CD block anchor missing')
ch1_new = (chr(9) + '/* luming: initialize clock defaults before any arm-io/soc-base dependency */\n'
    + chr(9) + 'bzero((void *)&gPEClockFrequencyInfo, sizeof(clock_frequency_info_t));\n'
    + chr(9) + 'gPEClockFrequencyInfo.timebase_frequency_hz = 24000000;\n'
    + chr(9) + 'gPEClockFrequencyInfo.bus_clock_rate_hz = 100000000;\n'
    + chr(9) + 'gPEClockFrequencyInfo.cpu_clock_rate_hz = 400000000;\n'
    + chr(9) + 'gPEClockFrequencyInfo.dec_clock_rate_hz = 24000000;\n'
    + '#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
    + chr(9) + '/* luming: minimal UEFI DT - no SecureDT parsing (avoids hangs), defaults only */\n'
    + chr(9) + 'return;\n'
    + '#else\n'
    + chr(9) + 'if (pe_arm_get_soc_base_phys() == 0) {\n'
    + chr(9)*2 + 'return;\n'
    + chr(9) + '}\n')
t8 = t8.replace(ch1_old, ch1_new, 1)
ch1_end_old = ('\tgPEClockFrequencyInfo.bus_to_dec_rate_den =\n'
               '\t    gPEClockFrequencyInfo.bus_clock_rate_hz / gPEClockFrequencyInfo.dec_clock_rate_hz;\n}\n')
if t8.count(ch1_end_old) != 1:
    print('CH1-END-ANCHOR-MISSING', t8.count(ch1_end_old))
    raise SystemExit('pe_identify_machine end anchor missing')
t8 = t8.replace(ch1_end_old, ch1_end_old.replace('}\n', '#endif /* APPLE_ARM64_ARCH_FAMILY: luming early-returns above */\n}\n'), 1)
pim2.write_text(t8)
print('CH1-NO-DT-IN-PE-IDENTIFY-OK')
# CH2: pe_init_debug root/target-is-fpga DT lookup: Apple only
pgen = base / 'pexpert/gen/pe_gen.c'
t9 = pgen.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
ch2_old = ('\t} else {\n\t\tDTEntry         root;\n\t\tif (SecureDTLookupEntry(NULL, "/", &root) == kSuccess) {\n'
           '\t\t\tvoid const *prop = NULL;\n\t\t\tuint32_t size = 0;\n'
           '\t\t\tif (SecureDTGetProperty(root, "target-is-fpga", &prop, &size) == kSuccess) {\n'
           '\t\t\t\tdebug_cpu_performance_degradation_factor = 10;\n\t\t\t}\n\t\t}\n\t}\n')
if t9.count(ch2_old) != 1:
    print('CH2-ANCHOR-MISSING', t9.count(ch2_old))
    raise SystemExit('pe_init_debug DT block anchor missing')
ch2_new = ('\t} else {\n#if defined(APPLE_ARM64_ARCH_FAMILY)\n\t\tDTEntry         root;\n\t\tif (SecureDTLookupEntry(NULL, "/", &root) == kSuccess) {\n'
           '\t\t\tvoid const *prop = NULL;\n\t\t\tuint32_t size = 0;\n'
           '\t\t\tif (SecureDTGetProperty(root, "target-is-fpga", &prop, &size) == kSuccess) {\n'
           '\t\t\t\tdebug_cpu_performance_degradation_factor = 10;\n\t\t\t}\n\t\t}\n'
           '#else /* luming: minimal UEFI DT - skip root/target-is-fpga lookup */\n'
           '\t\tdebug_cpu_performance_degradation_factor = 1;\n#endif\n\t}\n')
t9 = t9.replace(ch2_old, ch2_new, 1)
pgen.write_text(t9)
print('CH2-PE-INIT-DEBUG-NO-DT-OK')
print('CH-LUMING-EARLY-DT-FREE-DONE')
# ---- CJ: luming ml_parse_cpu_topology single boot CPU topology (no SecureDT) ----
pmr2 = base / 'osfmk/arm64/machine_routines.c'
t10 = pmr2.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
cj_start = 'void\nml_parse_cpu_topology(void)\n{\n\tDTEntry entry, child __unused;'
if t10.count(cj_start) != 1:
    print('CJ-START-ANCHOR-MISSING', t10.count(cj_start))
    raise SystemExit('ml_parse_cpu_topology start anchor missing')
cj_luming = ('#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
    '\t/* luming: minimal UEFI DT - single boot CPU topology from MPIDR, skip all SecureDT /cpus parsing */\n'
    '\tuint64_t mpidr = __builtin_arm_rsr64("MPIDR_EL1");\n'
    '\tml_topology_cpu_t *cpu = &topology_info.cpus[0];\n'
    '\tml_topology_cluster_t *cluster = &topology_info.clusters[0];\n'
    '\n'
    '\tfor (int i = 0; i <= MAX_CPU_CLUSTER_PHY_ID; i++) {\n'
    '\t\tcluster_offsets[i] = -1;\n'
    '\t}\n'
    '\tcluster_offsets[0] = 0;\n'
    '\ttopology_info.version = CPU_TOPOLOGY_VERSION;\n'
    '\ttopology_info.num_cpus = 1;\n'
    '\ttopology_info.max_cpu_id = 0;\n'
    '\ttopology_info.num_clusters = 1;\n'
    '\ttopology_info.max_cluster_id = 0;\n'
    '\ttopology_info.max_die_id = 0;\n'
    '\ttopology_info.chip_revision = 0;\n'
    '\ttopology_info.cluster_types = (1U << CLUSTER_TYPE_SMP);\n'
    '\ttopology_info.cluster_type_num_cpus[CLUSTER_TYPE_SMP] = 1;\n'
    '\ttopology_info.cluster_type_num_clusters[CLUSTER_TYPE_SMP] = 1;\n'
    '\n'
    '\tcpu->cpu_id = 0;\n'
    '\tcpu->phys_id = (uint32_t)(mpidr & (MPIDR_AFF1_MASK | MPIDR_AFF0_MASK));\n'
    '\tcpu->cluster_id = 0;\n'
    '\tcpu->die_id = 0;\n'
    '\tcpu->cluster_type = CLUSTER_TYPE_SMP;\n'
    '\tcpu->die_cluster_id = MPIDR_CLUSTER_ID(cpu->phys_id);\n'
    '\tcpu->cluster_core_id = MPIDR_CPU_ID(cpu->phys_id);\n'
    '\n'
    '\tcluster->cluster_id = 0;\n'
    '\tcluster->cluster_type = CLUSTER_TYPE_SMP;\n'
    '\tcluster->first_cpu_id = 0;\n'
    '\tcluster->num_cpus = 1;\n'
    '\tcluster->cpu_mask = 1ULL;\n'
    '\n'
    '\ttopology_info.boot_cpu = cpu;\n'
    '\ttopology_info.boot_cluster = cluster;\n'
    '\n'
    '\tuint64_t cpuid = cpu->cpu_id;\n'
    '\t__builtin_arm_wsr64("TPIDR_EL0", cpuid & MACHDEP_TPIDR_CPUNUM_MASK);\n'
    '\tassert((cpuid & MACHDEP_TPIDR_CPUNUM_MASK) == cpuid);\n'
    '\t__builtin_arm_wsr64("TPIDRRO_EL0", 0);\n'
    '\treturn;\n'
    '#else\n'
    '\tDTEntry entry, child __unused;')
t10 = t10.replace(cj_start, 'void\nml_parse_cpu_topology(void)\n{\n' + cj_luming, 1)
cj_end = ('\t__builtin_arm_wsr64("TPIDR_EL0", cpuid & MACHDEP_TPIDR_CPUNUM_MASK);\n'
          '\tassert((cpuid & MACHDEP_TPIDR_CPUNUM_MASK) == cpuid);\n'
          '\t__builtin_arm_wsr64("TPIDRRO_EL0", 0);\n}\n')
if t10.count(cj_end) != 1:
    print('CJ-END-ANCHOR-MISSING', t10.count(cj_end))
    raise SystemExit('ml_parse_cpu_topology end anchor missing')
t10 = t10.replace(cj_end, cj_end.replace('}\n', '#endif /* !APPLE_ARM64_ARCH_FAMILY: luming single-CPU topology above */\n}\n'), 1)
pmr2.write_text(t10)
print('CJ-ML-PARSE-LUMING-TOPOLOGY-OK')
# ---- CL: arm_vm_prot_init luming path skips /chosen/memory-map DT parsing ----
pav = base / 'osfmk/arm64/arm_vm_init.c'
t11 = pav.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
cl_idx = t11.index('\nnoAuxKC:\n')
cl_head, cl_tail = t11[:cl_idx], t11[cl_idx:]
cl_ah = '\tDTEntry memory_map;\n\tint err;\n\n'
if cl_head.count(cl_ah) != 1:
    print('CL-ANCHOR-MISSING', cl_head.count(cl_ah))
    raise SystemExit('arm_vm_prot_init anchor missing')
cl_head = cl_head.replace(cl_ah, cl_ah + '#if defined(APPLE_ARM64_ARCH_FAMILY)\n', 1)
cl_luming = ('#else /* luming: minimal UEFI DT - skip /chosen/memory-map TrustCache/AuxKC parsing */\n'
             '\t(void)memory_map;\n\t(void)err;\n'
             '\tsegEXTRADATA = 0;\n\tsegSizeEXTRADATA = 0;\n'
             '\tsegTRUSTCACHE = 0;\n\tsegSizeTRUSTCACHE = 0;\n#endif\n')
t11 = cl_head + cl_luming + cl_tail
pav.write_text(t11)
print('CL-VM-PROT-INIT-LUMING-NO-DT-OK')
# ---- CT: PE_get_random_seed luming path is DT-free (CNTVCT seed) ----
pgen2 = base / 'pexpert/gen/pe_gen.c'
t12 = pgen2.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
ct_old = 'uint32_t\nPE_get_random_seed(unsigned char *dst_random_seed, uint32_t request_size)\n{\n\tDTEntry         entryP;'
if t12.count(ct_old) != 1:
    print('CT-RANDOM-SEED-ANCHOR-MISSING', t12.count(ct_old))
    raise SystemExit('PE_get_random_seed anchor missing')
ct_new = ('uint32_t\nPE_get_random_seed(unsigned char *dst_random_seed, uint32_t request_size)\n{\n'
          '#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
          '\t/* luming: minimal UEFI DT - seed from CNTVCT_EL0, no SecureDT lookups */\n'
          '\tuint64_t cnt = 0;\n'
          '\t__asm__ __volatile__("mrs %0, cntvct_el0" : "=r"(cnt));\n'
          '\tuint32_t n = (request_size < sizeof(cnt)) ? request_size : (uint32_t)sizeof(cnt);\n'
          '\tfor (uint32_t i = 0; i < n; i++) {\n'
          '\t\tdst_random_seed[i] = (unsigned char)(cnt >> (8 * i));\n'
          '\t}\n'
          '\treturn n;\n'
          '#endif\n'
          '\tDTEntry         entryP;')
t12 = t12.replace(ct_old, ct_new, 1)
pgen2.write_text(t12)
print('CT-PE-RANDOM-SEED-DT-FREE-OK')
# ---- CU: luming early_random is a dependency-free CNTVCT+counter generator ----
pru = base / 'osfmk/prng/prng_random.c'
t13 = pru.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
cu_h_old = 'uint64_t\nearly_random(void)\n{\n\tuint64_t result;'
if t13.count(cu_h_old) != 1:
    print('CU-EARLY-RANDOM-HEAD-ANCHOR-MISSING', t13.count(cu_h_old))
    raise SystemExit('early_random head anchor missing')
cu_h_new = ('uint64_t\nearly_random(void)\n{\n'
            '#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
            '\t/* luming: dependency-free early random (no bootseed/ccdigest/ccdrbg/ml_get_timebase) */\n'
            '\tstatic uint64_t cu_counter = 0;\n'
            '\tuint64_t cu_cnt = 0;\n'
            '\t__asm__ __volatile__("mrs %0, cntvct_el0" : "=r"(cu_cnt));\n'
            '\tcu_counter++;\n'
            '\treturn (cu_cnt * 6364136223846793005ULL) ^ (cu_counter * 1442695040888963407ULL);\n'
            '#else\n'
            '\tuint64_t result;')
t13 = t13.replace(cu_h_old, cu_h_new, 1)
cu_e_old = '\n\tread_erandom(&result, sizeof(result));\n\n\treturn result;\n}\n'
if t13.count(cu_e_old) != 1:
    print('CU-EARLY-RANDOM-END-ANCHOR-MISSING', t13.count(cu_e_old))
    raise SystemExit('early_random end anchor missing')
t13 = t13.replace(cu_e_old, cu_e_old.replace('\n}\n', '\n#endif /* !APPLE_ARM64_ARCH_FAMILY: luming dependency-free generator above */\n}\n'), 1)
pru.write_text(t13)
print('CU-EARLY-RANDOM-DEPENDENCY-FREE-OK')
# ---- CX: luming SECURITY_READ_ONLY_LATE variables writable (loader maps __DATA_CONST RO) ----
psk = base / 'libkern/libkern/section_keywords.h'
t14 = psk.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
cx_old = '#define MARK_AS_FIXUP_TEXT\n\n#endif /* _SECTION_KEYWORDS_H_ */'
if t14.count(cx_old) != 1:
    print('CX-SECTION-KEYWORDS-ANCHOR-MISSING', t14.count(cx_old))
    raise SystemExit('section_keywords anchor missing')
cx_new = ('#define MARK_AS_FIXUP_TEXT\n\n'
          '#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
          '/* luming: the minimal UEFI/DarwinBoot loader maps __DATA_CONST read-only; the\n'
          ' * SECURITY_READ_ONLY_LATE variables are written during early boot (e.g. physmap_base\n'
          ' * in arm_vm_init), so keep them in writable data for non-Apple builds. */\n'
          '#undef __security_const_late\n'
          '#define __security_const_late\n'
          '#endif /* !APPLE_ARM64_ARCH_FAMILY */\n\n'
          '#endif /* _SECTION_KEYWORDS_H_ */')
t14 = t14.replace(cx_old, cx_new, 1)
psk.write_text(t14)
print('CX-LATE-CONST-WRITABLE-OK')
# ---- DD: luming early video/MMIO identity mapping in start.s bootstrap tables ----
pss = base / 'osfmk/arm64/start.s'
t15 = pss.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
dd_anchor = '/*\n *  arg0 - virtual start address\n *  arg1 - physical start address\n'
if t15.count(dd_anchor) != 1:
    raise SystemExit('DD start.s macro anchor missing')
dev_macros = '#if !defined(APPLE_ARM64_ARCH_FAMILY)\n/* DD (luming): Device-nGnRnE bootstrap mapping helpers for the video/MMIO window. */\n.macro create_l2_block_entries_dev\n\tand\t\t$4,\t$0, #(ARM_TT_L2_INDEX_MASK)\n\tlsr\t\t$4, $4, #(ARM_TTE_BLOCK_L2_SHIFT)\n\tlsl\t\t$4, $4, #(TTE_SHIFT)\n\tadd\t\t$4, $2, $4\n\tmov\t\t$5, #(ARM_TTE_VALID | ARM_TTE_BLOCK_AF | ARM_TTE_BLOCK_ATTRINDX(CACHE_ATTRINDX_DISABLE))\n\tand\t\t$6, $1, #(ARM_TTE_BLOCK_L2_MASK)\n\torr\t\t$6, $5, $6\n\tmov\t\t$5, $3\n\tmov\t\t$7, #(ARM_TT_L2_SIZE)\n1:\n\tstr\t\t$6, [$4], #(1 << TTE_SHIFT)\n\tadd\t\t$6, $6, $7\n\tsubs\t$5, $5, #1\n\tb.ne\t1b\n.endmacro\n.macro create_bootstrap_mapping_dev\n\tand\t$5, $0, #(ARM_TT_L2_INDEX_MASK)\n\tlsr\t$5, $5, #(ARM_TT_L2_SHIFT)\n\tmov\t$6, #(TTE_PGENTRIES)\n\tsub\t$5, $6, $5\n3:\tadd\t$4, $4, PGBYTES\n\tcreate_l1_table_entry\t$0, $3, $4, $6, $7, $8\n\tcmp\t$2, $5\n\tcsel\t$5, $2, $5, lt\n\tcreate_l2_block_entries_dev\t$0, $1, $4, $5, $6, $7, $8, $9\n\tsubs\t$2, $2, $5\n\tbeq\t2f\n\tadd \t$0, $0, $5, lsl #(ARM_TT_L2_SHIFT)\n\tadd \t$1, $1, $5, lsl #(ARM_TT_L2_SHIFT)\n\tmov\t$5, #(TTE_PGENTRIES)\n\tb\t3b\n2:\n.endmacro\n#endif /* !APPLE_ARM64_ARCH_FAMILY */\n\n'
t15 = t15[:t15.index(dd_anchor)] + dev_macros + t15[t15.index(dd_anchor):]
dd_vp = '\tcreate_bootstrap_mapping x0,  x4,  x5, x1, x2, x6, x10, x11, x12, x13\n'
if t15.count(dd_vp) != 1:
    raise SystemExit('DD start.s V=P mapping anchor missing')
dd_map = '\tcreate_bootstrap_mapping x0,  x4,  x5, x1, x2, x6, x10, x11, x12, x13\n#if !defined(APPLE_ARM64_ARCH_FAMILY)\n\t/* DD: identity-map the video/MMIO window 0xC0000000-0xFFFFFFFF (1 GiB, 2 MiB blocks)\n\t * as Device-nGnRnE in the V=P bootstrap tables so the early framebuffer console\n\t * (base 0xFC900000) can draw before arm_vm_init builds the final IO mappings. */\n\tMOV64\tx0, 0xC0000000\n\tmov\tx4, x0\n\tmov\tx5, #(0x40000000 >> ARM_TT_L2_SHIFT)\n\tcreate_bootstrap_mapping_dev x0, x4, x5, x1, x2, x6, x10, x11, x12, x13\n#endif /* !APPLE_ARM64_ARCH_FAMILY */\n'
t15 = t15[:t15.index(dd_vp)] + dd_map + t15[t15.index(dd_vp) + len(dd_vp):]
pss.write_text(t15)
print('DD-EARLY-VIDEO-MAPPING-OK')
# ---- DO: luming source-level kernel_bootstrap/halt diagnostics ----
pst = base / 'osfmk/kern/startup.c'
t16 = pst.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
if 'kernel_bootstrap: load_context returned' in t16:
    raise SystemExit('DO already applied')
kb = t16.find('kernel_bootstrap(')
if kb < 0:
    raise SystemExit('DO kernel_bootstrap not found')
rel = t16[kb:].find('\n\tload_context(thread);\n')
if rel < 0:
    raise SystemExit('DO load_context anchor not found after kernel_bootstrap')
pos = kb + rel
old = '\n\tload_context(thread);\n'
ins = '\n\tload_context(thread);\n\n#if !defined(APPLE_ARM64_ARCH_FAMILY)\n\t/* luming: load_context must not return; panic instead of continuing into halt/restart */\n\tpanic("kernel_bootstrap: load_context returned");\n#endif /* !APPLE_ARM64_ARCH_FAMILY */\n'
t16 = t16[:pos] + ins + t16[pos + len(old):]
pst.write_text(t16)
halt_file = None
for cand in ('osfmk/kern/startup.c', 'osfmk/arm64/machine_routines.c', 'osfmk/kern/sched_prim.c'):
    fp = base / cand
    if not fp.is_file():
        continue
    tc = fp.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    h = tc.find('halt_all_cpus(')
    if h >= 0 and tc[h:h+6000].count('PEHaltRestart(kPERestartCPU);') >= 1:
        halt_file = (fp, tc, h)
        break
if halt_file is None:
    print('DO-HALT-PEHALTRESTART-NOT-FOUND')
else:
    fp, tc, h = halt_file
    seg = tc[h:h+6000]
    oldh = '\tPEHaltRestart(kPERestartCPU);\n'
    newh = ('#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
            '\t/* luming: do not reset; freeze here after the halt printf */\n'
            '\tfor (;;) {\n\t\t__asm__ volatile("wfe");\n\t}\n'
            '#else\n\tPEHaltRestart(kPERestartCPU);\n#endif /* !APPLE_ARM64_ARCH_FAMILY */\n')
    seg2 = seg.replace(oldh, newh, 1)
    tc = tc[:h] + seg2 + tc[h+6000:]
    fp.write_text(tc)
    print('DO-HALT-FREEZE-OK', fp.name)
print('DO-SOURCE-DIAGNOSTICS-OK')
# ---- DX: luming single-CPU-safe ml_cpu_down (no IPI-failure panic) ----
pmc = base / 'osfmk/arm64/machine_routines.c'
t17 = pmc.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
if 'DX-SINGLE-CPU-ML-CPU-DOWN' in t17:
    print('DX already applied')
else:
    mark17 = '\tcpu_data_ptr->cpu_running = FALSE;\n'
    try:
        i17 = t17.index('ml_cpu_down(')
        j17 = t17.index(mark17, i17)
    except ValueError:
        raise SystemExit('DX ml_cpu_down anchor missing')
    ins17 = ('\n#if !defined(APPLE_ARM64_ARCH_FAMILY)\n'
             '\t/* DX-SINGLE-CPU-ML-CPU-DOWN: luming has no other CPUs to IPI/migrate to;\n'
             '\t * return instead of the remote-timer-migration IPI-failure panic. */\n'
             '\treturn;\n'
             '#endif /* !APPLE_ARM64_ARCH_FAMILY */\n')
    t17 = t17[:j17+len(mark17)] + ins17 + t17[j17+len(mark17):]
    pmc.write_text(t17)
    print('DX-SINGLE-CPU-ML-CPU-DOWN')
print('DX-SOURCE-DIAGNOSTICS-OK')
# ---- FK: luming source-level early console (RAM log + post-TTBR framebuffer flush) ----
def fk_edit(path, fn):
    fp = base / path
    if not fp.is_file():
        print('FK-MISSING-FILE', path)
        return None
    t = fp.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    return fp, t
helper = ''''''
rv = fk_edit('osfmk/arm64/arm_vm_init.c', 1)
if rv is None:
    raise SystemExit('FK arm_vm_init missing')
fp, t = rv
if 'luming_log' not in t:
    idx = t.find('#include ')
    t = t[:idx] + helper + t[idx:]
    def ins_after(anchor, text):
        global t
        if anchor in t: t = t.replace(anchor, anchor + text, 1); print('FK-ANCHOR-OK')
        else: print('FK-ANCHOR-MISS', anchor[:40])
    ins_after('gVirtBase = args->virtBase;\n', '\tluming_log(1);\n')
    ins_after('physmap_base = phystokv(args->topOfKernelData);\n', '\tluming_log(2);\n')
    ins_after('physmap_base += physmap_slide;\n', '\tluming_log(3);\n')
    pos = t.find('set_mmu_ttb(')
    if pos >= 0:
        e = t.find(';', pos)
        t = t[:e+1] + '\n\tluming_log(4);\n\tluming_flush();\n' + t[e+1:]
        print('FK-TTB-OK')
    else:
        print('FK-TTB-MISS')
    fp.write_text(t)
for path, anch, code in (('osfmk/kern/startup.c', 'void\nmachine_startup(boot_args *args)\n{', '\tluming_log(11); luming_flush();\n'),
                          ('osfmk/kern/startup.c', 'void\nkernel_bootstrap(void)\n{', '\tluming_log(13); luming_flush();\n'),
                          ('osfmk/kern/startup.c', 'void\nkernel_bootstrap_thread(void)\n{', '\tluming_log(16); luming_flush();\n'),
                          ('osfmk/arm64/machine_routines.c', 'void\nmachine_conf(void)\n{', '\tluming_log(12); luming_flush();\n')):
    rv = fk_edit(path, 1)
    if rv is None: continue
    fp2, t2 = rv
    if 'luming_log' not in t2:
        t2 = t2.replace('#include ', 'extern void luming_log(unsigned); extern void luming_flush(void);\n#include ', 1)
    if anch in t2:
        t2 = t2.replace(anch, anch + code, 1)
        print('FK-INSERT-OK', path)
    else:
        print('FK-INSERT-MISS', path)
    fp2.write_text(t2)
print('FK-SOURCE-EARLY-CONSOLE-OK')
print('LUMING-CLOCK-FIXES-DONE')
PY
"""
    return script[:start] + injection + captured + script[end:]


def inputs(base, with_geni=False):
    evidence = json.loads((DEVICE / 'reports/xnu-8796-sequence/results.json').read_text(encoding='utf-8'))
    if evidence['base_head'] != BASE_COMMIT or evidence['source_head'] != XNU_COMMIT:
        raise ValueError('Review evidence pins differ')
    selection = [item for item in evidence['selection'] if item['status'] == 'virtual-pass']
    if not evidence['complete_sequence'] or len(selection) != 48:
        raise ValueError('Need the complete reviewed 48-patch selection')
    patches = []
    for item in selection:
        name = item['patch']
        if Path(name).name != name or not name.endswith('.patch'):
            raise ValueError('Unsafe patch name')
        candidate = DEVICE / 'patches/xnu-8796-candidates' / name
        if not candidate.exists():
            directory = 'kits/NativePrelink/patches' if name.startswith('xnu-10063.') else 'patches/xnu'
            candidate = base / directory / name
        if sha(candidate) != item['sha256']:
            raise ValueError(f'Reviewed patch changed: {name}')
        patches.append(candidate)
    baseline = base / 'scripts/build-xnu-4k-baseline.sh'
    firehose = base / 'scripts/build-firehose.sh'
    if sha(baseline) != BASELINE_HASH or sha(firehose) != FIREHOSE_HASH:
        raise ValueError('Unexpected upstream build scripts')
    script = baseline.read_text(encoding='utf-8')
    script = replace_exact(script, '.unstripped.noctf', '.unstripped', 3)
    script = replace_exact(script, '-miphoneos-version-min=17.0', '-miphoneos-version-min=16.0', 1)
    script = replace_exact(script, '-Wl,-ios_version_min,17.0', '-Wl,-ios_version_min,16.0', 1)
    script = capture_kernel_make(script)
    firehose_script = replace_exact(firehose.read_text(encoding='utf-8'),
                                   'IPHONEOS_DEPLOYMENT_TARGET=17.0',
                                   'IPHONEOS_DEPLOYMENT_TARGET=16.0', 1)
    for name in ('clang17-zero-init.patch', 'clang17-uio-stackbuf.patch',
                 'clang-compile-records.patch',
                 'disabled-kperf-ast.patch', 'disabled-kperf-timer.patch',
                 'disabled-kperf-scheduler.patch',
                 'disabled-kperf-ktrace.patch',
                 'disabled-kpc-task.patch',
                 'disabled-kpc-context.patch',
                 'zzzz-ci-public-profiling.patch', 'zzzz-display-probe-feature.patch'):
        compatibility = DEVICE / 'patches/xnu-8796-ci-candidates' / name
        if not compatibility.is_file():
            raise ValueError(f'Missing reviewed clang17 compatibility patch: {name}')
        patches.append(compatibility)
    donor_bridge = DEVICE / 'patches/xnu-8796-donor-candidates/vfs-getvfs-with-vfsops.patch'
    if not donor_bridge.is_file():
        raise ValueError('Missing reviewed real VFS donor bridge')
    patches.append(donor_bridge)
    if with_geni:
        geni_patch = DEVICE / 'patches/xnu-8796-geni' / GENI_PATCH_NAME
        if not geni_patch.is_file():
            raise ValueError('Missing reviewed GENI UART patch')
        if sha(geni_patch) != GENI_PATCH_HASH:
            raise ValueError(f'Reviewed GENI patch changed: {GENI_PATCH_NAME}')
        patches.append(geni_patch)
    return patches, script, firehose_script


def run(command, **kwargs):
    print('+', ' '.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--with-ufs', action='store_true')
    parser.add_argument('--with-geni', action='store_true',
                        help='UFS variant plus the reviewed polled QCOM GENI UART console patch')
    args = parser.parse_args()
    if args.with_geni and not args.with_ufs:
        raise SystemExit('--with-geni requires --with-ufs')
    base = args.base.resolve()
    patches, script, firehose_script = inputs(base, args.with_geni)
    summary = {'base_commit': BASE_COMMIT, 'xnu_commit': XNU_COMMIT,
               'patches': [{'name': p.name, 'sha256': sha(p)} for p in patches],
               'baseline_adapter_sha256': hashlib.sha256(script.encode()).hexdigest(),
               'firehose_adapter_sha256': hashlib.sha256(firehose_script.encode()).hexdigest(),
               'reviewed_base_patch_count': 48,
               'ci_compatibility_patch_count': sum(p.parent.name == 'xnu-8796-ci-candidates' for p in patches),
               'donor_compatibility_patch_count': sum(p.parent.name == 'xnu-8796-donor-candidates' for p in patches),
               'scope': 'source compile/link only; optional device archives disabled',
               'kernel_built': False, 'prelinked': False, 'device_verified': False}
    summary['with_geni'] = bool(args.with_geni)
    summary['geni_patch'] = ({'name': GENI_PATCH_NAME, 'sha256': GENI_PATCH_HASH}
                             if args.with_geni else None)
    if args.check:
        shell = ('C:/Program Files/Git/bin/bash.exe' if platform.system() == 'Windows'
                 else shutil.which('bash'))
        if not shell:
            raise ValueError('bash is required to syntax-check the adapted recipes')
        for content in (script, firehose_script):
            subprocess.run([shell, '-n'], input=content, text=True, encoding='utf-8', check=True)
        summary['adapted_shell_syntax'] = 'passed; no build commands executed'
        print(json.dumps(summary, indent=2))
        return
    if platform.system() != 'Darwin' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Build mode requires the macOS Actions runner; use --check locally')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    if temporary == DEVICE or temporary.is_relative_to(DEVICE):
        raise ValueError('RUNNER_TEMP must be outside the device repository')
    work = temporary / 'nuwa-8796-source'
    work.mkdir(exist_ok=False)
    (work / 'refer').mkdir()
    (work / 'reports').mkdir()
    report = work / 'reports/build.json'
    report.write_text(json.dumps(summary, indent=2) + '\n')
    shutil.copytree(base / 'scripts', work / 'scripts')
    for name in ('drivers', 'kits'):
        if (base / name).is_dir():
            shutil.copytree(base / name, work / name)
    if (DEVICE / 'include').is_dir():
        shutil.copytree(DEVICE / 'include', work / 'include')
    patched_builder = DEVICE / 'scripts' / 'build-arm-driver.py'
    if patched_builder.is_file():
        shutil.copyfile(patched_builder, work / 'scripts' / 'build-arm-driver.py')
    for path in patches:
        folder = 'kits/NativePrelink/patches' if path.name.startswith('xnu-10063.') else 'patches/xnu'
        target = work / folder / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    (work / 'scripts/build-xnu-4k-baseline.sh').write_text(script, newline='\n')
    (work / 'scripts/build-firehose.sh').write_text(firehose_script, newline='\n')
    iig_adapter = work / 'scripts/iig-darwin22.py'
    adapter_source = DEVICE / 'scripts/ci/iig-darwin22.py'
    iig_adapter.write_text(f'#!{sys.executable}\n' + adapter_source.read_text(), newline='\n')
    iig_adapter.chmod(0o755)
    real_iig = subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--find', 'iig'], text=True).strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('XNU_', 'UNIXNU_'))}
    env.update(UNIXNU_XNU_TAG='xnu-8796.141.3', UNIXNU_XNU_COMMIT=XNU_COMMIT,
               UNIXNU_XNU_DARWIN='22.6.0', UNIXNU_XNU_VERSION='8796.141.3',
               UNIXNU_DRIVERKIT_TAG='xnu-8796.141.3', UNIXNU_TRUSTCACHE_TAG='xnu-8796.141.3',
               XNU_BOOT_PROGRESS='0', XNU_ZONE_PGZ_ALIGNMENT_PROBE='0', JOBS='3',
               IIG=str(iig_adapter), NUWA_REAL_IIG=real_iig)
    summary['iig_adapter_sha256'] = sha(adapter_source)
    summary['iig_compatibility'] = 'pinned generator inline RPC branches; deployment-target candidate rejected'
    try:
        # Reuse the base recipe: real source checkout, ordered patches, installhdrs,
        # real libfirehose archive and XNU build. No stubs or suppressed link errors.
        run(['bash', work / 'scripts/build-xnu-4k-baseline.sh'], cwd=work, env=env)
        kernel = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped'
        magic, cpu, subtype, filetype = struct.unpack('<IIII', kernel.read_bytes()[:16])
        if (magic, cpu, subtype, filetype) != (0xfeedfacf, 0x100000c, 0, 2):
            raise ValueError('Expected ordinary ARM64 MH_EXECUTE kernel')
        summary.update(kernel_built=True, kernel_sha256=sha(kernel),
                       kernel_path=str(kernel.relative_to(work)))
        run(['file', kernel])
        run(['xcrun', 'otool', '-l', kernel])
        if args.with_ufs:
            summary['nativeufs_patches'] = [
                {'name': name, 'sha256': sha(DEVICE / 'patches/nativeufs' / name)}
                for name in ('0001-nativeufs-sm8735-probe-gcc.patch',
                             '0002-nativeufs-stage0-snapshot.patch')]
            ufs_source = work / 'drivers/NativeUfs/NativeUfs.cpp'
            if ufs_source.is_file():
                # NativeUfs source patches first: the staging injection below calls
                # nativeUfsStageStop(), which 0002 defines (and stage 0 lives inside
                # hostBringUp). The patches are reviewed under patches/nativeufs/.
                for patch_name in ('0001-nativeufs-sm8735-probe-gcc.patch',
                                   '0002-nativeufs-stage0-snapshot.patch'):
                    patch = DEVICE / 'patches/nativeufs' / patch_name
                    if not patch.is_file():
                        raise ValueError(f'missing NativeUfs patch {patch}')
                    run(['patch', '-p1', '--forward', '--batch', '-i', str(patch)],
                        cwd=work / 'drivers/NativeUfs')
                inserts = (
                    ('  gRootDiagnostic.stage = "DMA allocation";', 1),
                    ('  gRootDiagnostic.stage = "SMMU and request list";', 2),
                    ('  if (!hostStartQueue (why)) return false;', 3),
                    ('  gRootDiagnostic.stage = "NOP OUT";', 4),
                    ('  gRootDiagnostic.stage = "power mode query";', 5),
                    ('  gRootDiagnostic.stage = "LUN ready";', 6),
                )
                text = ufs_source.read_text(encoding='utf-8')
                for anchor, stage in inserts:
                    if text.count(anchor) != 1:
                        raise SystemExit(f'hostBringUp anchor count for stage {stage}: {text.count(anchor)}')
                    text = text.replace(
                        anchor,
                        f'  if (nativeUfsStageStop ({stage}, why)) {{ return false; }}\n{anchor}')
                ufs_source.write_text(text, encoding='utf-8')
            storage_headers = work / 'build/storage-abi/include/IOKit/storage'
            device_headers = DEVICE / 'include/IOStorageFamily'
            if device_headers.is_dir():
                storage_headers.mkdir(parents=True, exist_ok=True)
                for header in device_headers.glob('*.h'):
                    shutil.copyfile(header, storage_headers / header.name)
                sdk = Path(subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip())
                shutil.copyfile(sdk / 'usr/include/TargetConditionals.h',
                                work / 'build/storage-abi/include/TargetConditionals.h')
            for component in ('ArmDTPlatform', 'ArmCpu', 'NativeUfs'):
                run([sys.executable, '-B', work / 'scripts/build-arm-driver.py', component],
                    cwd=work, env=env)
            run([sys.executable, '-B', DEVICE / 'scripts/build-native-ufs-bindings.py'],
                cwd=work, env=env)
            archives = [work / 'build/armdt-platform/libArmDTPlatform.a',
                        work / 'build/armcpu/libArmCpu.a',
                        work / 'build/native-ufs/libNativeUfs.a']
            for archive in archives:
                if not archive.is_file():
                    raise ValueError(f'missing archive {archive}')
            captured = json.loads((work / 'reports/kernel-make-command.json').read_text())
            kernel.unlink()
            noctf = kernel.with_suffix('.unstripped.noctf')
            if noctf.exists():
                noctf.unlink()
            force = ' '.join(f'-Wl,-force_load,{archive}' for archive in archives)
            firehose_dir = work / '.cache/fakeroot/usr/local/lib/kernel'
            firehose = firehose_dir / 'libfirehose_kernel.a'
            if firehose.is_file():
                force = f'-L{firehose_dir} -lfirehose_kernel ' + force
                archives.insert(0, firehose)
            dependencies = ' '.join(str(archive) for archive in archives)
            relink = captured + [f'LD_KERNEL_ARCHIVES={force}',
                                 f'XNU_EXTERNAL_KERNEL_ARCHIVES={dependencies}']
            relink_env = dict(env)
            relink_env['KERNEL_SDKROOT'] = str(work / '.cache/fakeroot')
            relink_env['CPATH'] = str(work / '.cache/fakeroot/usr/local/include') + os.pathsep + env.get('CPATH', '')
            run(relink, cwd=work, env=relink_env)
            magic, cpu, subtype, filetype = struct.unpack('<IIII', kernel.read_bytes()[:16])
            if (magic, cpu, subtype, filetype) != (0xfeedfacf, 0x100000c, 0, 2):
                raise ValueError('Relinked kernel is not an ARM64 MH_EXECUTE')
            summary['ufs_archives'] = [str(archive.relative_to(work)) for archive in archives]
            summary['kernel_sha256'] = sha(kernel)
            kernel_bytes = kernel.read_bytes()
            summary['ufs_compiled_markers'] = {
                name: name.encode() in kernel_bytes for name in (
                    'qcom,sm8735-ufshc', 'qcom,ufshc',
                    'NATIVE_UFS stage 0',
                    'firmware did not leave UFS powered',
                    'firmware did not leave UFS bus clocks enabled')}
            summary['ufs_required_markers'] = 'all ufs_compiled_markers must be true'
        try:
            run([sys.executable, '-B', DEVICE / 'scripts/ci/audit-driver-archives.py',
                 '--work', work, '--base', base])
            summary['driver_archive_audit'] = 'passed; compile and symbol closure only'
        except subprocess.CalledProcessError:
            if not args.with_ufs:
                raise
            summary['driver_archive_audit'] = 'failed (experimental UFS archive closure; kernel linked)'
        # Reuse this exact build's argv and real archives. Runtime feature flags,
        # crypto/pthread providers and the startup timer branch remain unchanged.
        summary['baseline_kernel_sha256'] = sha(kernel)
        try:
            run([sys.executable, '-B', DEVICE / 'scripts/ci/platform-link-audit.py',
                 '--work', work])
            summary['platform_link_audit'] = 'passed; ArmCpu + ArmDTPlatform linked, no hardware execution'
        except subprocess.CalledProcessError:
            if not args.with_ufs:
                raise
            summary['platform_link_audit'] = 'failed (experimental UFS archive closure; kernel linked)'
        summary['kernel_sha256'] = sha(kernel)
        if args.with_geni:
            kernel_bytes = kernel.read_bytes()
            summary['geni_compiled_markers'] = {
                name: name.encode() in kernel_bytes for name in (
                    'qcom,geni-uart', 'qcom_geni_uart_transmit_ready',
                    'qcom_geni_uart_serial_functions')}
            summary['geni_required_markers'] = 'all geni_compiled_markers must be true'
    finally:
        source = work / '.cache/xnu-work'
        if source.is_dir():
            summary['source_status'] = subprocess.run(
                ['git', '-C', str(source), 'status', '--short'], text=True,
                capture_output=True, check=False).stdout
            original_rpc = work / 'refer/xnu/iokit/DriverKit/IORPC.h'
            built_rpc = source / 'iokit/DriverKit/IORPC.h'
            if original_rpc.exists() and built_rpc.exists():
                summary['iorpc_header_unchanged'] = sha(original_rpc) == sha(built_rpc)
            generated = list((work / 'build').rglob('*.iig.cpp'))
            summary['iig_generated_files'] = len(generated)
            summary['iig_kernel_content_references'] = [str(p.relative_to(work)) for p in generated
                if 'rpc.kernelContent' in p.read_text(errors='replace') or '.kernelContent =' in p.read_text(errors='replace')]
        summary['object_count'] = len(list((work / 'build').rglob('*.o')))
        artifact = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837.unstripped'
        summary['linked_kernel_present'] = artifact.is_file()
        if artifact.is_file():
            summary['linked_kernel_bytes'] = artifact.stat().st_size
            summary['linked_kernel_sha256'] = sha(artifact)
        target_objects = work / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
        summary['xnu_target_object_count'] = len(list(target_objects.rglob('*.o')))
        records = list(target_objects.rglob('*.o.json')) + list(target_objects.rglob('*.cpo.json'))
        summary['compile_record_count'] = len(records)
        summary['required_compile_records'] = {}
        for name in ('startup.o.json', 'kern_exec.o.json', 'IOPlatformExpert.cpo.json', 'prng_random.o.json'):
            matches = list(target_objects.rglob(name))
            summary['required_compile_records'][name] = [
                {'path': str(path.relative_to(work)), 'sha256': sha(path)} for path in matches]
            for path in matches:
                target = work / 'reports/compile-records' / path.relative_to(target_objects)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        summary['key_objects'] = {name: [
            {'path': str(path.relative_to(work)), 'bytes': path.stat().st_size, 'sha256': sha(path)}
            for path in sorted(target_objects.rglob(name))]
            for name in ('ast.o', 'processor.o')}
        summary['iig_adaptations'] = [json.loads(p.read_text()) for p in
                                      sorted((work / 'build').rglob('*.darwin22.json'))]
        # Preserve unrecognized templates on failure; generated source stays in
        # cloud artifacts/ignored local out/, never in the tracked device tree.
        for path in (work / 'build').rglob('*.iig.cpp'):
            if 'kernelContent' in path.read_text(errors='replace'):
                target = work / 'reports/iig-failures' / path.relative_to(work / 'build')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        report.write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
