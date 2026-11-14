# Hardware Topology

**Expose. Explore. Explain.**

Hardware Topology is a read-only Linux hardware-topology inspector for CPU/core/cache locality, PCI devices and driver binding, NUMA structure and locality, and IOMMU groups.

Current release: **0.0.8**

Repository: <https://github.com/linspectacles/hardware-topology>

<p align="center">
  <a href="https://github.com/brunonlinespace/brunonlinespace/blob/main/linspectacles/hardware-topology.png">
    <img
      src="https://raw.githubusercontent.com/brunonlinespace/brunonlinespace/main/linspectacles/hardware-topology.png"
      alt="Hardware Topology screenshot"
      width="900"
    >
  </a>
</p>

## What Hardware Topology is

Hardware Topology answers a focused question: **how are CPUs, caches, NUMA nodes and PCI devices related, and what locality or binding information does the kernel expose?**

Hardware Topology is inspired in part by **Microsoft Sysinternals Coreinfo**, particularly its emphasis on making processor topology understandable. LinSpectacles extends that idea through Linux-native sysfs data for caches, NUMA locality, PCI devices and IOMMU groups.

CPU vulnerability and mitigation state is intentionally no longer part of this applet; that kernel interface now belongs to the separate **[CPU Vulnerabilities](https://github.com/linspectacles/cpu-vulnerabilities)** applet.

The applet reports information available from its documented Linux sources. Data that is unavailable, restricted, or not reported by those sources remains explicitly unavailable rather than being inferred.

## Highlights

- Four focused views: **CPU Topology**, **PCI Devices**, **NUMA Nodes** and **IOMMU Groups**.
- CPU package/socket, core, logical-CPU, thread-sibling and cache-sharing topology from sysfs.
- CPU-to-NUMA locality where exposed by the kernel.
- PCI identity, driver/module binding, NUMA locality, IOMMU group, IRQ, resources and runtime/link attributes where available.
- Optional `lspci -D -mm -nn` enrichment when pciutils is installed; numeric sysfs IDs remain available without it.
- NUMA node CPU maps, online/possible state, memory information, distance vectors, allocation statistics, VM counters and huge-page pools.
- IOMMU group membership with member PCI devices, drivers/modules and NUMA-node information where reported.
- Per-view search/filtering, sortable inventories and Metadata Details synchronized to the visible selection.
- Multi-view export uses data already collected in the current session; unopened views are not scanned in the background.

## Data sources and acquisition

### CPU topology and cache locality

- `/sys/devices/system/cpu/cpu*/topology/...`
- `/sys/devices/system/cpu/cpu*/cache/index*/...`
- `/sys/devices/system/cpu/cpu*/node*`

### PCI devices

- `/sys/bus/pci/devices/<PCI-address>/...`
- Optional `lspci -D -mm -nn` for human-readable vendor/device enrichment.

### NUMA

- `/sys/devices/system/node/possible`
- `/sys/devices/system/node/online`
- `/sys/devices/system/node/has_cpu`
- `/sys/devices/system/node/has_normal_memory`
- `/sys/devices/system/node/node*/cpulist`
- `/sys/devices/system/node/node*/cpumap`
- `/sys/devices/system/node/node*/meminfo`
- `/sys/devices/system/node/node*/distance`
- `/sys/devices/system/node/node*/numastat`
- `/sys/devices/system/node/node*/vmstat`
- `/sys/devices/system/node/node*/hugepages/hugepages-*/...`

### IOMMU

- `/sys/kernel/iommu_groups/<group>/devices/...`

The applet uses those sources according to the acquisition paths implemented in this release. `lspci` is optional enrichment only; the PCI inventory remains based on sysfs when pciutils is absent.

## Interface

Hardware Topology uses one LinSpectacles inventory + **Metadata Details** shell across four selectable views:

- **CPU Topology** — filterable by package.
- **PCI Devices** — filterable by driver.
- **NUMA Nodes** — structural node inventory without an additional category filter.
- **IOMMU Groups** — filterable by driver.

Changing view loads that inventory when it has not yet been collected. Search and filter changes rebuild only the visible inventory and keep Metadata Details synchronized with an actually visible selected record.

The **Options...** dialog provides:

- Appearance controls for Metadata Details visibility and layout.
- Sources documentation.
- Multi-view export of data already collected during the session.
- About and repository information.

Keyboard shortcuts implemented by this release include:

- `F3` — show/hide Metadata Details.
- `Ctrl+L` — toggle Horizontal/Vertical details layout.
- `Shift+F5` — refresh the active hardware inventory.
- `Ctrl+/` — open Options.

## Read-only and scope boundaries

Hardware Topology reports **structure, locality, binding and kernel-exposed topology**; it is not a live performance monitor.

NUMA counters and distance values are reported facts and are not converted into a performance or health verdict. NUMA distance is a kernel-reported relative cost, not a physical-distance, latency or bandwidth measurement.

Memory usage, pressure, process memory maps and VM activity remain Resource Monitor responsibilities.

A PCI device appearing in an IOMMU group does not by itself establish that it is safe or suitable for passthrough.

Missing NUMA or IOMMU interfaces are reported as unavailable rather than treated as a system fault.

CPU vulnerability and mitigation reporting is intentionally outside this applet from 0.0.7 and belongs to **CPU Vulnerabilities**.

## Running from source

The supplied source is directly runnable on Linux. The Python-side requirements are:

- Python 3.
- PyQt6 for the graphical interface.

Create a local virtual environment if you do not already have the required Python packages in the environment you want to use:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install PyQt6
python3 linspectacles_hardware_topology/standalone.py
```

`lspci` from pciutils is optional and is used only for human-readable PCI vendor/device enrichment. The sysfs-based PCI inventory still works without it.

No RPM installation instructions are included in this README.

## LinSpectacles Hub

This applet can run independently, but it can also be installed and used inside **[LinSpectacles Hub](https://github.com/linspectacles/linspectacles-hub)**.

LinSpectacles Hub is the optional workbench for the LinSpectacles applet family. It provides shared applet discovery, navigation and management while each inspector remains independently runnable.

To install a portable or user applet through the Hub, open **Configuration... → Applets** and add the applet ZIP. Once discovered, the applet can be enabled and opened from the Hub navigation.

System-installed applets are discovered by the Hub from its system applet store and remain managed by the system package manager.

## Source layout and LinSpectacles integration

The applet source is contained in:

```text
linspectacles_hardware_topology/
├── __init__.py
├── standalone.py
├── LICENSE
├── applet.json
├── README.md
└── hardware_topology/
    ├── applet.py
    ├── _support.py
    └── icon.png
```

`applet.json` defines the applet's LinSpectacles identity and integration metadata. `standalone.py` provides direct standalone launching, while the applet entrypoint allows the same inspector to run inside LinSpectacles Hub.

## Configuration

Hardware Topology persists its implemented presentation preferences, including Metadata Details visibility/layout and per-view column visibility through the host integration. Persisted UI state does not modify hardware, drivers, NUMA policy or IOMMU configuration.

## Scope

Hardware Topology is intentionally about **hardware structure and locality**.

For CPU vulnerability and mitigation state reported by `/sys/devices/system/cpu/vulnerabilities/*`, use **[CPU Vulnerabilities](https://github.com/linspectacles/cpu-vulnerabilities)**.

Related runtime domains such as resource usage and pressure remain with their respective LinSpectacles specialists rather than being inferred here.

## License

GNU General Public License v3.0 or later. See [`LICENSE`](LICENSE).

## Links

- LinSpectacles organisation: <https://github.com/linspectacles/>
- LinSpectacles Hub: <https://github.com/linspectacles/linspectacles-hub>
- Repository: <https://github.com/linspectacles/hardware-topology>
- Issues: <https://github.com/linspectacles/hardware-topology/issues>

<p align="center">
  <a href="https://github.com/linspectacles/hardware-topology">
    <img
      src="hardware_topology/icon.png"
      alt="Hardware Topology"
      width="256"
    >
  </a>
</p>
