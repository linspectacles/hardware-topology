#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LinSpectacles - Linux Inspection Suite
# Copyright (C) 2026 brunonlinespace
# GPL-3.0-or-later

APPLET_ID = "linspectacles-hardware-topology"
APPLET_NAME = "Hardware Topology"
APPLET_VERSION = "0.0.8"
APPLET_AUTHOR = "brunonlinespace"
APPLET_EDITOR = "brunonlinespace"
APPLET_PUBLISHER = "brunonlinespace"
APPLET_ORGANIZATION_ID = "linspectacles"
APPLET_ORGANIZATION_URL = "https://github.com/linspectacles/"
APPLET_REPOSITORY = "https://github.com/linspectacles/hardware-topology"

import re
import shlex
import subprocess
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QUrl
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ._support import BaseInspectorTab, export_records_dialog, metadata_font_qss


_VIEW_ORDER = ("cpu", "pci", "numa", "iommu")
_VIEW_SPECS = {
    "cpu": {
        "label": "CPU Topology",
        "counter": "Total Logical CPUs",
        "filter_label": "Package:",
        "filter_all": "All Packages",
        "columns": [
            ("cpu", "CPU", 70, "right"),
            ("package", "Package", 85, "right"),
            ("core", "Core", 70, "right"),
            ("numa_node", "NUMA Node", 90, "right"),
            ("online", "Online", 80, "left"),
            ("thread_siblings", "Thread Siblings", 150, "left"),
        ],
        "numeric": {"cpu", "package", "core"},
    },
    "pci": {
        "label": "PCI Devices",
        "counter": "Total PCI Devices",
        "filter_label": "Driver:",
        "filter_all": "All Drivers",
        "columns": [
            ("address", "PCI Address", 135, "left"),
            ("device", "Device", 220, "left"),
            ("vendor", "Vendor", 180, "left"),
            ("driver", "Driver", 135, "left"),
            ("numa_node", "NUMA Node", 90, "right"),
            ("iommu_group", "IOMMU Group", 100, "right"),
        ],
        "numeric": set(),
    },
    "numa": {
        "label": "NUMA Nodes",
        "counter": "Total NUMA Nodes",
        "filter_label": "",
        "filter_all": "",
        "columns": [
            ("node", "Node", 75, "right"),
            ("cpus", "CPUs", 150, "left"),
            ("memory_total", "Memory Total", 125, "right"),
            ("memory_free", "Memory Free", 125, "right"),
            ("numa_hit", "NUMA Hit", 105, "right"),
            ("numa_miss", "NUMA Miss", 105, "right"),
            ("distance", "Distance", 185, "left"),
        ],
        "numeric": {"node", "memory_total", "memory_free", "numa_hit", "numa_miss"},
    },
    "iommu": {
        "label": "IOMMU Groups",
        "counter": "Total IOMMU Groups",
        "filter_label": "Driver:",
        "filter_all": "All Drivers",
        "columns": [
            ("group", "Group", 75, "right"),
            ("device_count", "Device Count", 100, "right"),
            ("pci_devices", "PCI Devices", 245, "left"),
            ("drivers", "Drivers", 190, "left"),
            ("numa_nodes", "NUMA Nodes", 130, "left"),
        ],
        "numeric": {"group", "device_count"},
    },
}


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _read_link_name(path):
    try:
        target = Path(path).resolve(strict=True)
        return target.name
    except (FileNotFoundError, PermissionError, OSError):
        return ""


def _natural_number(text, default=-1):
    try:
        return int(str(text).strip(), 10)
    except (TypeError, ValueError):
        return default


def _format_bytes(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(number)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024.0
    if unit == "B":
        return f"{number:,} B"
    return f"{amount:.2f} {unit}"


def _parse_kib(value):
    match = re.search(r"(-?\d+)\s*kB\b", str(value), re.IGNORECASE)
    if not match:
        return None
    return max(0, int(match.group(1))) * 1024


def _cpu_node(cpu_dir):
    try:
        candidates = sorted(
            p.name for p in cpu_dir.iterdir()
            if p.name.startswith("node") and p.name[4:].isdigit()
        )
    except (FileNotFoundError, PermissionError, OSError):
        return "Not reported"
    if not candidates:
        return "Not reported"
    return candidates[0][4:]


def _cpu_cache_rows(cpu_dir):
    rows = []
    cache_root = cpu_dir / "cache"
    try:
        indexes = sorted(
            (p for p in cache_root.iterdir() if p.is_dir() and p.name.startswith("index")),
            key=lambda p: _natural_number(p.name[5:], 999),
        )
    except (FileNotFoundError, PermissionError, OSError):
        return rows
    for index in indexes:
        rows.append({
            "index": index.name,
            "level": _read_text(index / "level") or "Not reported",
            "type": _read_text(index / "type") or "Not reported",
            "size": _read_text(index / "size") or "Not reported",
            "shared_cpu_list": _read_text(index / "shared_cpu_list") or "Not reported",
            "coherency_line_size": _read_text(index / "coherency_line_size") or "Not reported",
            "ways_of_associativity": _read_text(index / "ways_of_associativity") or "Not reported",
            "number_of_sets": _read_text(index / "number_of_sets") or "Not reported",
        })
    return rows


def _collect_cpu_topology():
    root = Path("/sys/devices/system/cpu")
    try:
        cpu_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"cpu\d+", p.name)),
            key=lambda p: int(p.name[3:]),
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []

    rows = []
    for cpu_dir in cpu_dirs:
        cpu = int(cpu_dir.name[3:])
        topology = cpu_dir / "topology"
        online_raw = _read_text(cpu_dir / "online")
        online = "Yes" if online_raw in {"", "1"} else "No"
        package_raw = _read_text(topology / "physical_package_id")
        core_raw = _read_text(topology / "core_id")
        package = _natural_number(package_raw, -1)
        core = _natural_number(core_raw, -1)
        package_display = package if package >= 0 else "Not reported"
        core_display = core if core >= 0 else "Not reported"
        rows.append({
            "cpu": cpu,
            "package": package_display,
            "core": core_display,
            "numa_node": _cpu_node(cpu_dir),
            "online": online,
            "thread_siblings": _read_text(topology / "thread_siblings_list") or "Not reported",
            "core_cpus": _read_text(topology / "core_cpus_list") or _read_text(topology / "core_siblings_list") or "Not reported",
            "die_id": _read_text(topology / "die_id") or "Not reported",
            "cluster_id": _read_text(topology / "cluster_id") or "Not reported",
            "book_id": _read_text(topology / "book_id") or "Not reported",
            "drawer_id": _read_text(topology / "drawer_id") or "Not reported",
            "caches": _cpu_cache_rows(cpu_dir),
            "_filter_values": [str(package_display)],
        })
    return rows


def _lspci_labels():
    labels = {}
    try:
        result = subprocess.run(
            ["lspci", "-D", "-mm", "-nn"],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return labels
    if result.returncode != 0:
        return labels
    for line in result.stdout.splitlines():
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) < 4:
            continue
        labels[parts[0]] = {
            "class_label": parts[1],
            "vendor_label": parts[2],
            "device_label": parts[3],
            "subsystem_vendor_label": parts[4] if len(parts) > 4 else "",
            "subsystem_device_label": parts[5] if len(parts) > 5 else "",
        }
    return labels


def _pci_record(device_dir, labels=None):
    labels = labels or {}
    address = device_dir.name
    info = labels.get(address, {})
    vendor_id = _read_text(device_dir / "vendor") or "Not reported"
    device_id = _read_text(device_dir / "device") or "Not reported"
    subsystem_vendor_id = _read_text(device_dir / "subsystem_vendor") or "Not reported"
    subsystem_device_id = _read_text(device_dir / "subsystem_device") or "Not reported"
    class_code = _read_text(device_dir / "class") or "Not reported"
    driver = _read_link_name(device_dir / "driver") or "Unbound"
    module = _read_link_name(device_dir / "driver" / "module") or "Not reported"
    iommu_group = _read_link_name(device_dir / "iommu_group") or "Not exposed"
    numa_raw = _read_text(device_dir / "numa_node")
    numa_node = numa_raw if numa_raw != "" else "Not reported"

    resource_lines = []
    resource_text = _read_text(device_dir / "resource")
    if resource_text:
        resource_lines = [line.strip() for line in resource_text.splitlines() if line.strip()]

    return {
        "address": address,
        "device": info.get("device_label") or device_id,
        "vendor": info.get("vendor_label") or vendor_id,
        "driver": driver,
        "numa_node": numa_node,
        "iommu_group": iommu_group,
        "vendor_id": vendor_id,
        "device_id": device_id,
        "subsystem_vendor_id": subsystem_vendor_id,
        "subsystem_device_id": subsystem_device_id,
        "class_code": class_code,
        "class_label": info.get("class_label") or "Not reported",
        "subsystem_vendor_label": info.get("subsystem_vendor_label") or "Not reported",
        "subsystem_device_label": info.get("subsystem_device_label") or "Not reported",
        "kernel_module": module,
        "modalias": _read_text(device_dir / "modalias") or "Not reported",
        "irq": _read_text(device_dir / "irq") or "Not reported",
        "revision": _read_text(device_dir / "revision") or "Not reported",
        "local_cpulist": _read_text(device_dir / "local_cpulist") or "Not reported",
        "current_link_speed": _read_text(device_dir / "current_link_speed") or "Not reported",
        "current_link_width": _read_text(device_dir / "current_link_width") or "Not reported",
        "max_link_speed": _read_text(device_dir / "max_link_speed") or "Not reported",
        "max_link_width": _read_text(device_dir / "max_link_width") or "Not reported",
        "runtime_status": _read_text(device_dir / "power" / "runtime_status") or "Not reported",
        "power_state": _read_text(device_dir / "power_state") or "Not reported",
        "resources": resource_lines,
        "_filter_values": [driver],
    }


def _collect_pci_devices():
    root = Path("/sys/bus/pci/devices")
    labels = _lspci_labels()
    try:
        devices = sorted((p for p in root.iterdir() if p.exists()), key=lambda p: p.name)
    except (FileNotFoundError, PermissionError, OSError):
        return []
    return [_pci_record(device, labels) for device in devices]


def _parse_node_meminfo(text):
    rows = []
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        label = re.sub(r"^Node\s+\d+\s+", "", left).strip()
        rows.append((label, value.strip()))
    return rows


def _parse_numastat(text):
    rows = []
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            rows.append((parts[0], parts[1]))
    return rows


def _parse_key_value_numbers(text):
    rows = []
    for line in (text or "").splitlines():
        parts = line.split(None, 1)
        if not parts:
            continue
        key = parts[0]
        raw = parts[1].strip() if len(parts) > 1 else ""
        try:
            value = int(raw, 10)
        except (TypeError, ValueError):
            value = raw
        rows.append((key, value))
    return rows


def _parse_index_set(text):
    """Parse Linux list syntax such as '0-3,8,10-11' into integer IDs."""
    result = set()
    for token in str(text or "").split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            try:
                start, end = int(left, 10), int(right, 10)
            except ValueError:
                continue
            if end < start:
                start, end = end, start
            result.update(range(start, end + 1))
        else:
            try:
                result.add(int(token, 10))
            except ValueError:
                continue
    return result


def _membership_state(node, raw_list):
    if not str(raw_list or "").strip():
        return "Not reported"
    return "Yes" if int(node) in _parse_index_set(raw_list) else "No"


def _collect_node_hugepages(node_dir):
    rows = []
    root = node_dir / "hugepages"
    try:
        directories = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: _natural_number(re.search(r"(\d+)", p.name).group(1), 0)
            if re.search(r"(\d+)", p.name) else 0,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return rows

    for directory in directories:
        match = re.fullmatch(r"hugepages-(\d+)kB", directory.name)
        if not match:
            continue
        size_kib = int(match.group(1))
        values = {}
        for key in ("nr_hugepages", "free_hugepages", "surplus_hugepages", "nr_overcommit_hugepages", "demote_size"):
            raw = _read_text(directory / key)
            if raw == "":
                continue
            values[key] = _natural_number(raw, raw)
        rows.append({
            "page_size_kib": size_kib,
            "page_size": _format_bytes(size_kib * 1024),
            "total": values.get("nr_hugepages", "Not reported"),
            "free": values.get("free_hugepages", "Not reported"),
            "surplus": values.get("surplus_hugepages", "Not reported"),
            "overcommit": values.get("nr_overcommit_hugepages", "Not reported"),
            "demote_size": values.get("demote_size", "Not reported"),
        })
    return rows


def _collect_numa_nodes(root=Path("/sys/devices/system/node")):
    root = Path(root)
    try:
        node_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"node\d+", p.name)),
            key=lambda p: int(p.name[4:]),
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []

    node_ids = [int(p.name[4:]) for p in node_dirs]
    possible_raw = _read_text(root / "possible")
    online_raw = _read_text(root / "online")
    has_cpu_raw = _read_text(root / "has_cpu")
    has_normal_memory_raw = _read_text(root / "has_normal_memory")

    rows = []
    for node_dir in node_dirs:
        node = int(node_dir.name[4:])
        meminfo = _parse_node_meminfo(_read_text(node_dir / "meminfo"))
        memdict = dict(meminfo)
        total = _parse_kib(memdict.get("MemTotal", ""))
        free = _parse_kib(memdict.get("MemFree", ""))

        raw_distance = _read_text(node_dir / "distance")
        distance_values = raw_distance.split()
        distances = []
        if distance_values:
            for target, value in zip(node_ids, distance_values):
                try:
                    parsed = int(value, 10)
                except ValueError:
                    parsed = value
                distances.append((target, parsed))
            distance = ", ".join(f"{target}:{value}" for target, value in distances)
        else:
            distance = "Not exposed"

        numastat = _parse_numastat(_read_text(node_dir / "numastat"))
        statdict = {key: value for key, value in numastat}
        numa_hit = _natural_number(statdict.get("numa_hit", ""), -1)
        numa_miss = _natural_number(statdict.get("numa_miss", ""), -1)

        rows.append({
            "node": node,
            "cpus": _read_text(node_dir / "cpulist") or "Not reported",
            "memory_total": total,
            "memory_free": free,
            "numa_hit": numa_hit if numa_hit >= 0 else "Not reported",
            "numa_miss": numa_miss if numa_miss >= 0 else "Not reported",
            "distance": distance,
            "distances": distances,
            "cpu_map": _read_text(node_dir / "cpumap") or "Not reported",
            "possible": _membership_state(node, possible_raw),
            "online": _membership_state(node, online_raw),
            "has_cpu": _membership_state(node, has_cpu_raw),
            "has_normal_memory": _membership_state(node, has_normal_memory_raw),
            "meminfo": meminfo,
            "numastat": numastat,
            "vmstat": _parse_key_value_numbers(_read_text(node_dir / "vmstat")),
            "hugepages": _collect_node_hugepages(node_dir),
            "source_path": str(node_dir),
            "_filter_values": [],
        })
    return rows


def _collect_iommu_groups():
    root = Path("/sys/kernel/iommu_groups")
    labels = _lspci_labels()
    try:
        group_dirs = sorted(
            (p for p in root.iterdir() if p.is_dir() and p.name.isdigit()),
            key=lambda p: int(p.name),
        )
    except (FileNotFoundError, PermissionError, OSError):
        return []

    rows = []
    for group_dir in group_dirs:
        devices_dir = group_dir / "devices"
        try:
            members = sorted((p for p in devices_dir.iterdir()), key=lambda p: p.name)
        except (FileNotFoundError, PermissionError, OSError):
            members = []
        member_records = []
        for member in members:
            real = Path("/sys/bus/pci/devices") / member.name
            if real.exists():
                member_records.append(_pci_record(real, labels))
            else:
                member_records.append({
                    "address": member.name,
                    "device": "Not reported",
                    "vendor": "Not reported",
                    "driver": "Unbound",
                    "numa_node": "Not reported",
                    "kernel_module": "Not reported",
                })
        drivers = sorted({r.get("driver", "Unbound") for r in member_records})
        numa_nodes = sorted({str(r.get("numa_node", "Not reported")) for r in member_records})
        rows.append({
            "group": int(group_dir.name),
            "device_count": len(member_records),
            "pci_devices": ", ".join(r.get("address", "") for r in member_records) or "None reported",
            "drivers": ", ".join(drivers) if drivers else "None reported",
            "numa_nodes": ", ".join(numa_nodes) if numa_nodes else "None reported",
            "members": member_records,
            "_filter_values": drivers,
        })
    return rows


def _populate_sources_canvas(canvas, content):
    """Apply the Journal-family Sources treatment without altering source text."""
    source_heading = QTextCharFormat()
    source_heading.setForeground(QColor("#9cdcfe"))
    source_heading.setFontWeight(600)
    source_body = QTextCharFormat()
    source_body.setForeground(QColor("#dcdcdc"))
    source_body.setFontWeight(400)

    canvas.clear()
    cursor = canvas.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    for line in content.splitlines(keepends=True):
        # Keep the existing descriptions and their indentation byte-for-byte.
        # Non-indented section/source headings use the family blue treatment.
        fmt = source_heading if line.strip() and not line[:1].isspace() else source_body
        cursor.insertText(line, fmt)
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    canvas.setTextCursor(cursor)
    canvas.verticalScrollBar().setValue(0)


class HardwareTopologyOptionsDialog(QDialog):
    """Established LinSpectacles Options treatment for Hardware Topology."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Options — Hardware Topology")
        self.resize(980, 720)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # Appearance
        appearance_page = QWidget()
        appearance_layout = QVBoxLayout(appearance_page)
        appearance_layout.setContentsMargins(12, 12, 12, 12)
        appearance_layout.setSpacing(10)

        heading = QLabel("Record details")
        heading_font = heading.font()
        heading_font.setBold(True)
        heading.setFont(heading_font)
        appearance_layout.addWidget(heading)

        note = QLabel(
            "Choose how the hardware inventory and Metadata Details are arranged."
        )
        note.setWordWrap(True)
        appearance_layout.addWidget(note)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Record details layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Horizontal", "Vertical"])
        self.layout_combo.setCurrentText(owner.details_orientation.title())
        self.layout_combo.currentTextChanged.connect(self._layout_changed)
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch(1)
        appearance_layout.addLayout(layout_row)

        self.show_metadata_panel = QCheckBox("Show Metadata Details panel (F3)")
        self.show_metadata_panel.setChecked(owner.metadata_panel_visible)
        self.show_metadata_panel.toggled.connect(self._metadata_panel_changed)
        appearance_layout.addWidget(self.show_metadata_panel)

        shortcut_note = QLabel(
            "Shortcuts: F3 hides/restores Metadata Details; "
            "Ctrl+L toggles Horizontal/Vertical layout; "
            "Shift+F5 refreshes the active hardware inventory; "
            "Ctrl+/ opens Options."
        )
        shortcut_note.setWordWrap(True)
        appearance_layout.addWidget(shortcut_note)
        appearance_layout.addStretch(1)
        tabs.addTab(appearance_page, "Appearance")

        # Sources
        sources_page = QWidget()
        sources_layout = QVBoxLayout(sources_page)
        sources_layout.setContentsMargins(12, 12, 12, 12)
        sources_layout.setSpacing(10)

        sources_heading = QLabel("Data Sources")
        sources_heading_font = sources_heading.font()
        sources_heading_font.setBold(True)
        sources_heading_font.setPointSize(max(sources_heading_font.pointSize(), 11))
        sources_heading.setFont(sources_heading_font)
        sources_layout.addWidget(sources_heading)

        sources_note = QLabel(
            "Linux interfaces used by this read-only hardware-topology inspector."
        )
        sources_note.setWordWrap(True)
        sources_layout.addWidget(sources_note)

        sources_text = QTextEdit()
        sources_text.setReadOnly(True)
        sources_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        chosen_font = getattr(owner, "_metadata_font", None)
        font_rule = metadata_font_qss(chosen_font) if chosen_font is not None else ""
        if chosen_font is not None:
            sources_text.setFont(chosen_font)
            sources_text.document().setDefaultFont(chosen_font)
            sources_text.setCurrentFont(chosen_font)
        sources_text.setStyleSheet(
            "QTextEdit { background: #151719; color: #dcdcdc; border: 1px solid #333333; "
            f"{font_rule} "
            "}"
        )
        _populate_sources_canvas(sources_text, owner.sources_text())
        sources_layout.addWidget(sources_text, 1)
        tabs.addTab(sources_page, "Sources")

        # Export
        export_page = QWidget()
        export_layout = QVBoxLayout(export_page)
        export_layout.setContentsMargins(12, 12, 12, 12)
        export_layout.setSpacing(10)

        export_heading = QLabel("Export")
        export_heading_font = export_heading.font()
        export_heading_font.setBold(True)
        export_heading.setFont(export_heading_font)
        export_layout.addWidget(export_heading)

        export_note = QLabel(
            "Choose which Hardware Topology views to include in one export. "
            "Export uses data already collected in this session; an unopened view is not scanned in the background."
        )
        export_note.setWordWrap(True)
        export_layout.addWidget(export_note)

        self.export_view_checks = {}
        for key, label in owner.export_view_specs():
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.export_view_checks[key] = checkbox
            export_layout.addWidget(checkbox)

        self.export_metadata = QCheckBox("Metadata Details")
        self.export_metadata.setChecked(False)
        export_layout.addWidget(self.export_metadata)
        export_layout.addStretch(1)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_button = QPushButton("Export Selected Views...")
        export_button.clicked.connect(self._export_selected)
        export_row.addWidget(export_button)
        export_layout.addLayout(export_row)
        tabs.addTab(export_page, "Export")

        # About
        about_page = QWidget()
        about_layout = QVBoxLayout(about_page)
        about_layout.setContentsMargins(24, 20, 24, 18)
        about_layout.setSpacing(8)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(112, 112)
        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        112,
                        112,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        about_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        name = QLabel("<h2 style='margin:0'>Hardware Topology</h2>")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(name)

        version = QLabel(f"Version {APPLET_VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(version)

        slogan = QLabel("<b>Expose. Explore. Explain.</b>")
        slogan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(slogan)

        description = QLabel(
            "A read-only LinSpectacles applet for CPU/core/cache topology, PCI device binding and locality, "
            "NUMA node layout, allocation/locality details and huge-page pools, IOMMU group membership, "
            "without changing device state."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(description)

        details = QLabel(
            "<b>Build:</b> Standalone LinSpectacles applet<br>"
            "<b>Organisation ID:</b> "
            f"<a href='{APPLET_ORGANIZATION_URL}'>{APPLET_ORGANIZATION_ID}</a><br>"
            f"<b>Applet ID:</b> {APPLET_ID}"
        )
        details.setWordWrap(True)
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        details.setOpenExternalLinks(True)
        details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(details)

        copyright_label = QLabel(
            "Copyright © 2026 "
            "<a href='https://github.com/brunonlinespace'>brunonlinespace</a>"
        )
        copyright_label.setOpenExternalLinks(True)
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(copyright_label)

        license_label = QLabel("Licensed under GNU GPLv3 or later.")
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(license_label)

        root_dir = Path(__file__).resolve().parent.parent
        readme_url = QUrl.fromLocalFile(str(root_dir / "README.md")).toString()
        license_url = QUrl.fromLocalFile(str(root_dir / "LICENSE")).toString()
        links = QLabel(
            f"<a href='{readme_url}'>README</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{license_url}'>License</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{APPLET_REPOSITORY}'>Repository</a>"
        )
        links.setOpenExternalLinks(True)
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(links)
        about_layout.addStretch(1)
        tabs.addTab(about_page, "About")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _layout_changed(self, text):
        self.owner.set_details_orientation(str(text).strip().lower(), persist=True)

    def _metadata_panel_changed(self, visible):
        self.owner.set_metadata_panel_visible(bool(visible), persist=True)

    def _export_selected(self):
        selected = [
            key for key, checkbox in self.export_view_checks.items()
            if checkbox.isChecked()
        ]
        self.owner.export_selected_views(
            selected,
            include_metadata=self.export_metadata.isChecked(),
            parent=self,
        )


class HardwareTopologyTab(BaseInspectorTab):
    """One Kernel-Modules-style inspector shell for four related hardware-topology views."""

    NUMERIC_COLUMNS = set()
    BYTE_COLUMNS = {"memory_total", "memory_free"}

    def __init__(self, parent=None):
        initial = _VIEW_SPECS["cpu"]
        super().__init__(
            initial["counter"],
            initial["columns"],
            [initial["filter_all"]],
            parent,
        )
        self.export_title = APPLET_NAME
        self._settings = QSettings("brunonlinespace", "Hardware Topology")
        self._datasets = {key: [] for key in _VIEW_ORDER}
        self._loaded_views = set()
        self._current_view = "cpu"
        self._host_column_callback = None
        self._host_hidden_all = set()

        # Established selector/action position: View -> Refresh List -> Options...
        self.view_label = QLabel("View:")
        self.view_label.setStyleSheet("font-weight: 600;")
        self.view_combo = QComboBox()
        self.view_combo.addItems([_VIEW_SPECS[key]["label"] for key in _VIEW_ORDER])
        self.view_combo.setMinimumWidth(170)
        refresh_index = self.dash_layout.indexOf(self.refresh_btn)
        self.dash_layout.insertWidget(refresh_index, self.view_label)
        self.dash_layout.insertWidget(refresh_index + 1, self.view_combo)
        self.view_combo.currentTextChanged.connect(self._view_changed)

        self.export_btn.setText("Options...")
        try:
            self.export_btn.clicked.disconnect()
        except TypeError:
            pass
        self.export_btn.clicked.connect(self.show_options)
        self.export_btn.setEnabled(True)

        self.details_orientation = "horizontal"
        self._load_details_orientation()

        self.metadata_panel_visible = True
        self._load_metadata_panel_visibility()

        self._shortcuts = []
        self._install_shortcuts()

        self._configure_view("cpu", refresh=False)
        self.load_modules()

    def apply_host_settings(self, settings=None, column_visibility_callback=None):
        settings = settings or {}
        # Keep the authoritative Kernel Modules metadata-font behavior, while storing
        # dynamic-view column visibility as prefixed IDs in the Suite setting.
        super().apply_host_settings(
            {"metadata_font": settings.get("metadata_font", ""), "hidden_columns": []},
            None,
        )
        self._host_column_callback = column_visibility_callback if callable(column_visibility_callback) else None
        self._host_hidden_all = {str(item) for item in settings.get("hidden_columns", [])}
        self.column_visibility.set_changed_callback(self._column_visibility_changed)
        self._apply_view_hidden_columns()

    def _view_prefix(self, view=None):
        return str(view or self._current_view)

    def _visibility_columns(self):
        prefix = self._view_prefix()
        return [
            (f"{prefix}:{column_id}", label, width, alignment)
            for column_id, label, width, alignment in self.columns
        ]

    def _apply_view_hidden_columns(self):
        if not hasattr(self, "column_visibility"):
            return
        self.column_visibility.columns = self._visibility_columns()
        prefix = self._view_prefix() + ":"
        current = [item for item in self._host_hidden_all if item.startswith(prefix)]
        self.column_visibility.apply_hidden(current)

    def _column_visibility_changed(self, hidden_current):
        prefix = self._view_prefix() + ":"
        self._host_hidden_all = {item for item in self._host_hidden_all if not item.startswith(prefix)}
        self._host_hidden_all.update(str(item) for item in hidden_current)
        if callable(self._host_column_callback):
            self._host_column_callback(sorted(self._host_hidden_all))

    def _view_key_from_label(self, label):
        for key in _VIEW_ORDER:
            if _VIEW_SPECS[key]["label"] == label:
                return key
        return "cpu"

    def _view_changed(self, label):
        key = self._view_key_from_label(label)
        self._configure_view(key, refresh=False)
        if key not in self._loaded_views:
            self.load_modules()
        else:
            self.modules_data = list(self._datasets[key])
            self._populate_filter_choices()
            self.filter_modules()
            self.set_status(f"Showing collected {_VIEW_SPECS[key]['label']} data.")

    def _configure_view(self, key, *, refresh=False):
        if key not in _VIEW_SPECS:
            key = "cpu"
        self._current_view = key
        spec = _VIEW_SPECS[key]
        self.total_label_text = spec["counter"]
        self.stats_label.setText(f"{self.total_label_text}: {len(self._datasets.get(key, []))}")
        self.columns = list(spec["columns"])
        self.NUMERIC_COLUMNS = set(spec["numeric"])
        self._sort_reverse = {}

        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([column[1] for column in self.columns])
        for index, (_cid, _label, width, _alignment) in enumerate(self.columns):
            self.table.setColumnWidth(index, width)
        self._apply_view_hidden_columns()

        self.category_label.setText(spec["filter_label"] or "Category:")
        show_filter = bool(spec["filter_label"])
        self.category_label.setVisible(show_filter)
        self.category_dropdown.setVisible(show_filter)
        self.search_entry.setPlaceholderText(f"Search {spec['label'].lower()}...")
        self.clear_info()

        if refresh:
            self.load_modules()

    def _populate_filter_choices(self):
        spec = _VIEW_SPECS[self._current_view]
        if not spec["filter_label"]:
            return
        values = sorted({
            str(value)
            for record in self.modules_data
            for value in record.get("_filter_values", [])
            if str(value).strip()
        }, key=str.lower)
        self.category_dropdown.blockSignals(True)
        self.category_dropdown.clear()
        self.category_dropdown.addItem(spec["filter_all"])
        self.category_dropdown.addItems(values)
        self.category_dropdown.setCurrentIndex(0)
        self.category_dropdown.blockSignals(False)

    def load_modules(self):
        key = self._current_view
        collectors = {
            "cpu": _collect_cpu_topology,
            "pci": _collect_pci_devices,
            "numa": _collect_numa_nodes,
            "iommu": _collect_iommu_groups,
        }
        try:
            records = collectors[key]()
        except Exception as exc:
            self.set_status(f"Error reading {_VIEW_SPECS[key]['label']}: {exc}")
            return

        self._datasets[key] = list(records)
        self._loaded_views.add(key)
        self.modules_data = list(records)
        self.stats_label.setText(f"{self.total_label_text}: {len(records)}")
        self._populate_filter_choices()
        self.filter_modules()

        if key == "numa" and not records:
            self.set_status(
                "No NUMA node directories are exposed. This can be normal on UMA/single-node systems."
            )
        elif key == "iommu" and not records:
            self.set_status(
                "No IOMMU groups are exposed. IOMMU may be unavailable, disabled, or not represented by this kernel."
            )
        else:
            self.set_status(f"Successfully refreshed {_VIEW_SPECS[key]['label']}.")

    def _reset_details_to_top(self):
        """Authoritative inventory-details convention: every render opens at the top."""
        cursor = self.info_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.info_text.setTextCursor(cursor)
        self.info_text.verticalScrollBar().setValue(0)

    def _synchronize_details_after_rebuild(self, matches):
        """Make the details pane describe the rebuilt inventory, never stale Qt selection state."""
        self.clear_info()

        signals_were_blocked = self.table.blockSignals(True)
        try:
            self.table.clearSelection()
            if not matches:
                self.table.setCurrentCell(-1, -1)
            else:
                # Do not rely on itemSelectionChanged here. Qt can keep row 0
                # current across two different datasets and therefore emit no
                # useful selection transition. Explicitly establish row 0.
                self.table.setCurrentCell(0, 0)
                self.table.selectRow(0)
        finally:
            self.table.blockSignals(signals_were_blocked)

        if matches:
            # Explicit rendering is the correctness guarantee. Selection signals
            # remain useful for real user clicks, but are never the only route.
            self._render_record_details(matches[0], self._current_view)
        self._reset_details_to_top()

    def filter_modules(self, *_):
        query = self.search_entry.text().strip().lower()
        spec = _VIEW_SPECS[self._current_view]
        selected = self.category_dropdown.currentText() if spec["filter_label"] else ""

        matches = []
        for record in self.modules_data:
            values = record.get("_filter_values", [])
            match_filter = (
                not spec["filter_label"]
                or selected == spec["filter_all"]
                or selected in [str(value) for value in values]
            )
            match_query = not query or any(
                query in str(value).lower()
                for key, value in record.items()
                if not str(key).startswith("_")
            )
            if match_filter and match_query:
                matches.append(record)

        # Block selection signals while replacing the model.  After the rebuild
        # we explicitly select *and render* the first visible record so details
        # cannot remain attached to the previous View/filter/search dataset.
        signals_were_blocked = self.table.blockSignals(True)
        try:
            self.clear_info()
            self.table.clearSelection()
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(matches))
            for row, record in enumerate(matches):
                for column_index, (column_id, _label, _width, alignment) in enumerate(self.columns):
                    value = record.get(column_id, "")
                    if column_id in self.BYTE_COLUMNS:
                        display = _format_bytes(value) if isinstance(value, int) else "Not reported"
                    else:
                        display = str(value)
                    item = QTableWidgetItem(display)
                    if column_index == 0:
                        item.setData(Qt.ItemDataRole.UserRole, record)
                    if alignment == "right":
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    else:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    self.table.setItem(row, column_index, item)

        finally:
            self.table.blockSignals(signals_were_blocked)

        self._synchronize_details_after_rebuild(matches)

        if query or (spec["filter_label"] and selected != spec["filter_all"]):
            self.filter_status_lbl.setText(f"(Showing {len(matches)} of {len(self.modules_data)} items)")
        else:
            self.filter_status_lbl.clear()
        self.export_btn.setEnabled(True)

    def sort_by(self, column_id):
        reverse = self._sort_reverse.get(column_id, False)
        if column_id in self.NUMERIC_COLUMNS:
            def key_func(item):
                value = item.get(column_id, -1)
                return value if isinstance(value, int) else _natural_number(value, -1)
        else:
            key_func = lambda item: str(item.get(column_id, "")).lower()
        try:
            self.modules_data.sort(key=key_func, reverse=reverse)
        except Exception:
            self.modules_data.sort(key=lambda item: str(item.get(column_id, "")).lower(), reverse=reverse)
        self._datasets[self._current_view] = list(self.modules_data)
        self._sort_reverse[column_id] = not reverse
        self.filter_modules()

    def on_module_select(self):
        record = self.current_record()
        if not record:
            return
        self._render_record_details(record, self._current_view)
        self._reset_details_to_top()
        primary = record.get(self.columns[0][0], "selected record")
        self.set_status(f"Inspecting {_VIEW_SPECS[self._current_view]['label']} record [{primary}].")

    def _render_record_details(self, record, view):
        self.clear_info()
        if view == "cpu":
            self._render_cpu_details(record)
        elif view == "pci":
            self._render_pci_details(record)
        elif view == "numa":
            self._render_numa_details(record)
        elif view == "iommu":
            self._render_iommu_details(record)
        self._reset_details_to_top()

    def _render_cpu_details(self, record):
        self.append_info("--- CPU Topology ---\n")
        for label, key in (
            ("Logical CPU", "cpu"),
            ("Online", "online"),
            ("Package / Socket ID", "package"),
            ("Core ID", "core"),
            ("NUMA Node", "numa_node"),
            ("Thread Siblings", "thread_siblings"),
            ("Core CPUs", "core_cpus"),
            ("Die ID", "die_id"),
            ("Cluster ID", "cluster_id"),
            ("Book ID", "book_id"),
            ("Drawer ID", "drawer_id"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- Cache Topology ---\n")
        caches = record.get("caches", [])
        if not caches:
            self.append_info("No cache-index topology is exposed for this logical CPU.\n")
        for index, cache in enumerate(caches, 1):
            label = f"L{cache.get('level', '?')} {cache.get('type', 'Cache')}"
            self.append_key_value(f"Cache {index}", label)
            self.append_key_value(f"Cache {index} Size", cache.get("size", "Not reported"))
            self.append_key_value(f"Cache {index} Shared CPUs", cache.get("shared_cpu_list", "Not reported"))
            self.append_key_value(f"Cache {index} Coherency Line", cache.get("coherency_line_size", "Not reported"))
            self.append_key_value(f"Cache {index} Ways", cache.get("ways_of_associativity", "Not reported"))
            self.append_key_value(f"Cache {index} Sets", cache.get("number_of_sets", "Not reported"))

    def _render_pci_details(self, record):
        self.append_info("--- PCI Identity ---\n")
        for label, key in (
            ("PCI Address", "address"),
            ("Class", "class_label"),
            ("Class Code", "class_code"),
            ("Vendor", "vendor"),
            ("Vendor ID", "vendor_id"),
            ("Device", "device"),
            ("Device ID", "device_id"),
            ("Subsystem Vendor", "subsystem_vendor_label"),
            ("Subsystem Vendor ID", "subsystem_vendor_id"),
            ("Subsystem Device", "subsystem_device_label"),
            ("Subsystem Device ID", "subsystem_device_id"),
            ("Revision", "revision"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- Kernel Binding ---\n")
        for label, key in (
            ("Driver", "driver"),
            ("Kernel Module", "kernel_module"),
            ("Modalias", "modalias"),
            ("IRQ", "irq"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- Topology ---\n")
        for label, key in (
            ("NUMA Node", "numa_node"),
            ("IOMMU Group", "iommu_group"),
            ("Local CPU List", "local_cpulist"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- PCI Link / Runtime ---\n")
        for label, key in (
            ("Current Link Speed", "current_link_speed"),
            ("Current Link Width", "current_link_width"),
            ("Maximum Link Speed", "max_link_speed"),
            ("Maximum Link Width", "max_link_width"),
            ("Runtime Power Status", "runtime_status"),
            ("Power State", "power_state"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        resources = record.get("resources", [])
        if resources:
            self.append_info("\n--- PCI Resources ---\n")
            for index, value in enumerate(resources):
                self.append_key_value(f"Resource {index}", value)

    def _render_numa_details(self, record):
        self.append_info("--- NUMA Node ---\n")
        for label, key in (
            ("Node", "node"),
            ("Online", "online"),
            ("Possible", "possible"),
            ("CPUs", "cpus"),
            ("CPU Map", "cpu_map"),
            ("Has CPU", "has_cpu"),
            ("Has Normal Memory", "has_normal_memory"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- Node Memory ---\n")
        meminfo = record.get("meminfo", [])
        if meminfo:
            for label, value in meminfo:
                self.append_key_value(label, value)
        else:
            self.append_info("No per-node memory information is exposed.\n")

        self.append_info("\n--- NUMA Allocation ---\n")
        stats = record.get("numastat", [])
        if stats:
            for label, value in stats:
                self.append_key_value(label, value)
        else:
            self.append_info("No per-node NUMA allocation statistics are exposed.\n")

        self.append_info("\n--- Distances ---\n")
        distances = record.get("distances", [])
        if distances:
            for target, value in distances:
                self.append_key_value(f"Target Node {target}", value)
            self.append_info(
                "Interpretation note: NUMA distance values are kernel-reported relative costs; "
                "they are not physical distance, latency, or bandwidth measurements.\n"
            )
        else:
            self.append_info("No NUMA distance vector is exposed for this node.\n")

        self.append_info("\n--- Huge Pages ---\n")
        hugepages = record.get("hugepages", [])
        if hugepages:
            for index, page in enumerate(hugepages, 1):
                prefix = f"Pool {index}"
                self.append_key_value(f"{prefix} Page Size", page.get("page_size", "Not reported"))
                self.append_key_value(f"{prefix} Total", page.get("total", "Not reported"))
                self.append_key_value(f"{prefix} Free", page.get("free", "Not reported"))
                self.append_key_value(f"{prefix} Surplus", page.get("surplus", "Not reported"))
                if page.get("overcommit", "Not reported") != "Not reported":
                    self.append_key_value(f"{prefix} Overcommit", page.get("overcommit"))
                if page.get("demote_size", "Not reported") != "Not reported":
                    self.append_key_value(f"{prefix} Demote Size", page.get("demote_size"))
        else:
            self.append_info("No per-node huge-page pools are exposed.\n")

        self.append_info("\n--- VM Statistics ---\n")
        vmstat = record.get("vmstat", [])
        if vmstat:
            for label, value in vmstat:
                self.append_key_value(label, value)
        else:
            self.append_info("No per-node VM statistics are exposed.\n")

        self.append_info("\n--- Source ---\n")
        self.append_key_value("Node Path", record.get("source_path", "Not reported"))
        self.append_info(
            "Interpretation note: Hardware Topology reports placement, allocation counters, "
            "relative distances and kernel-exposed node state. It does not infer NUMA performance or health.\n"
        )

    def _render_iommu_details(self, record):
        self.append_info("--- IOMMU Group ---\n")
        for label, key in (
            ("Group", "group"),
            ("Device Count", "device_count"),
            ("PCI Devices", "pci_devices"),
            ("Drivers", "drivers"),
            ("NUMA Nodes", "numa_nodes"),
        ):
            self.append_key_value(label, record.get(key, "Not reported"))

        self.append_info("\n--- Member PCI Devices ---\n")
        members = record.get("members", [])
        if not members:
            self.append_info("No member PCI devices are exposed for this group.\n")
        for index, member in enumerate(members, 1):
            self.append_key_value(f"Device {index} Address", member.get("address", "Not reported"))
            self.append_key_value(f"Device {index} Vendor", member.get("vendor", "Not reported"))
            self.append_key_value(f"Device {index} Device", member.get("device", "Not reported"))
            self.append_key_value(f"Device {index} Driver", member.get("driver", "Unbound"))
            self.append_key_value(f"Device {index} Kernel Module", member.get("kernel_module", "Not reported"))
            self.append_key_value(f"Device {index} NUMA Node", member.get("numa_node", "Not reported"))
        self.append_info(
            "\nInterpretation note: IOMMU group membership is reported topology. "
            "Hardware Topology does not infer whether a device is safe or suitable for passthrough.\n"
        )

    @staticmethod
    def sources_text():
        return (
            "Hardware Topology is read-only. Its displayed facts come from these interfaces.\n\n"
            "CPU package/core/thread-sibling topology and cache sharing\n"
            "  /sys/devices/system/cpu/cpu*/topology/...\n"
            "  /sys/devices/system/cpu/cpu*/cache/index*/...\n\n"
            "CPU-to-NUMA locality\n"
            "  /sys/devices/system/cpu/cpu*/node*\n\n"
            "PCI device identity, driver/module binding, NUMA locality, IOMMU group, IRQ, resources and runtime/link attributes\n"
            "  /sys/bus/pci/devices/<PCI-address>/...\n\n"
            "Optional human-readable PCI vendor/device enrichment when pciutils is available\n"
            "  lspci -D -mm -nn\n"
            "  Numeric sysfs IDs remain available when lspci is absent.\n\n"
            "NUMA node CPU maps, node availability, memory information, distances, allocation statistics, VM counters and huge-page pools\n"
            "  /sys/devices/system/node/possible\n"
            "  /sys/devices/system/node/online\n"
            "  /sys/devices/system/node/has_cpu\n"
            "  /sys/devices/system/node/has_normal_memory\n"
            "  /sys/devices/system/node/node*/cpulist\n"
            "  /sys/devices/system/node/node*/cpumap\n"
            "  /sys/devices/system/node/node*/meminfo\n"
            "  /sys/devices/system/node/node*/distance\n"
            "  /sys/devices/system/node/node*/numastat\n"
            "  /sys/devices/system/node/node*/vmstat\n"
            "  /sys/devices/system/node/node*/hugepages/hugepages-*/...\n\n"
            "IOMMU group membership\n"
            "  /sys/kernel/iommu_groups/<group>/devices/...\n\n"
            "Important distinctions\n"
            "  Hardware Topology reports structural locality and binding; it is not a live performance monitor.\n"
            "  NUMA counters and distance values are reported facts; they are not converted into a performance or health verdict.\n"
            "  Memory usage, pressure, process memory maps and VM activity remain Resource Monitor responsibilities.\n"
            "  A PCI device appearing in an IOMMU group does not by itself establish that it is safe or suitable for passthrough.\n"
            "  Missing NUMA or IOMMU interfaces are reported as unavailable rather than treated as a system fault."
        )

    def show_options(self):
        dialog = HardwareTopologyOptionsDialog(self)
        dialog.exec()

    def _add_shortcut(self, sequence, callback):
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)
        return shortcut

    def _install_shortcuts(self):
        self._add_shortcut("F3", self.toggle_metadata_panel)
        self._add_shortcut("Ctrl+L", self.toggle_details_orientation)
        self._add_shortcut("Shift+F5", self.load_modules)
        self._add_shortcut("Ctrl+/", self.show_options)

    def toggle_details_orientation(self):
        target = "vertical" if self.details_orientation == "horizontal" else "horizontal"
        self.set_details_orientation(target, persist=True)

    def _load_metadata_panel_visibility(self):
        value = self._settings.value("metadata_panel_visible", True)
        if isinstance(value, str):
            visible = value.strip().lower() not in {"0", "false", "no", "off"}
        else:
            visible = bool(value)
        self.set_metadata_panel_visible(visible, persist=False)

    def set_metadata_panel_visible(self, visible, *, persist=True):
        self.metadata_panel_visible = bool(visible)
        details_panel = self.splitter.widget(1)
        if details_panel is not None:
            details_panel.setVisible(self.metadata_panel_visible)
        if self.metadata_panel_visible:
            self.set_details_orientation(self.details_orientation, persist=False)
        if persist:
            self._settings.setValue("metadata_panel_visible", self.metadata_panel_visible)
            self._settings.sync()

    def toggle_metadata_panel(self):
        self.set_metadata_panel_visible(not self.metadata_panel_visible, persist=True)

    def _load_details_orientation(self):
        orientation = str(self._settings.value("details_orientation", "horizontal") or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.set_details_orientation(orientation, persist=False)

    def set_details_orientation(self, orientation, *, persist=True):
        orientation = str(orientation or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.details_orientation = orientation
        details_panel = self.splitter.widget(1)
        details_layout = details_panel.layout() if details_panel is not None else None
        if orientation == "vertical":
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            if details_layout is not None:
                details_layout.setContentsMargins(0, 8, 0, 0)
            self.splitter.setSizes([430, 330])
        else:
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            if details_layout is not None:
                details_layout.setContentsMargins(8, 0, 0, 0)
            self.splitter.setSizes([480, 700])
        if persist:
            self._settings.setValue("details_orientation", orientation)
            self._settings.sync()

    def export_view_specs(self):
        return [(key, _VIEW_SPECS[key]["label"]) for key in _VIEW_ORDER]

    @staticmethod
    def _append_export_view(flat_records, label, records, columns, metadata_renderer=None):
        records = list(records or [])
        if not records:
            flat_records.append({
                "view": label,
                "record": "",
                "field": "Status",
                "value": "No collected data.",
            })
            return
        for index, record in enumerate(records, 1):
            for column in columns:
                key, field_label = column[0], column[1]
                value = record.get(key, "")
                if key in {"memory_total", "memory_free"}:
                    value = _format_bytes(value) if isinstance(value, int) else "Not reported"
                flat_records.append({
                    "view": label,
                    "record": str(index),
                    "field": field_label,
                    "value": value,
                })
            if callable(metadata_renderer):
                flat_records.append({
                    "view": label,
                    "record": str(index),
                    "field": "Metadata Details",
                    "value": metadata_renderer(record),
                })

    def metadata_details_for_export(self, view, record):
        original_info = self.info_text
        temp = QTextEdit()
        self.info_text = temp
        try:
            self._render_record_details(record, view)
            return temp.toPlainText()
        finally:
            self.info_text = original_info

    def export_selected_views(self, selected, *, include_metadata=False, parent=None):
        selected = list(selected or [])
        if not selected:
            QMessageBox.information(parent or self, "Export", "Select at least one Hardware Topology view to export.")
            return

        flat_records = []
        for key in _VIEW_ORDER:
            if key not in selected:
                continue
            spec = _VIEW_SPECS[key]
            records = self._datasets[key] if key in self._loaded_views else []
            renderer = None
            if include_metadata:
                renderer = lambda record, view=key: self.metadata_details_for_export(view, record)
            self._append_export_view(flat_records, spec["label"], records, spec["columns"], renderer)

        path = export_records_dialog(
            parent or self,
            APPLET_NAME,
            flat_records,
            columns=[
                ("view", "View"),
                ("record", "Record"),
                ("field", "Field"),
                ("value", "Value"),
            ],
        )
        if path is not None:
            self.set_status(f"Exported selected Hardware Topology views to {path}")


def create_applet(parent=None):
    return HardwareTopologyTab(parent=parent)


def run_standalone():
    import sys
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMainWindow
    from ._support import STANDALONE_STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName(APPLET_NAME)
    app.setOrganizationName(APPLET_ORGANIZATION_ID)
    icon_path = Path(__file__).resolve().parent / "icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(STANDALONE_STYLESHEET)

    window = QMainWindow()
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowTitle(f"{APPLET_NAME} {APPLET_VERSION} — LinSpectacles Applet")
    window.resize(1200, 780)
    window.setCentralWidget(create_applet())
    window.show()
    return app.exec()
