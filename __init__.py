import os
import json
import re
import subprocess
import tempfile
import shutil
import concurrent.futures
import substance_painter
import substance_painter.textureset
import substance_painter.event
from PySide6 import QtWidgets, QtCore, QtGui

# --- CONFIGURATION ---
__version__ = "1.0.0"

# Dynamically locate the plugin's root directory
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

# Point to the bundled nvcompress.exe inside the "bin" subfolder
NVTT_EXECUTABLE = os.path.join(PLUGIN_DIR, "bin", "nvcompress.exe")

class NVTTWorker(QtCore.QThread):
    """Background thread to process DDS conversion and per-map image scaling."""
    progress_update = QtCore.Signal(int)
    finished_success = QtCore.Signal(int)
    finished_error = QtCore.Signal(str)

    def __init__(self, exported_files, temp_dir, final_out_dir, exe_path, global_res, global_format, ts_settings, mipmap_options):
        super().__init__()
        self.exported_files = exported_files
        self.temp_dir = temp_dir
        self.final_out_dir = final_out_dir
        self.exe_path = exe_path
        self.global_res = global_res
        self.global_format = global_format 
        self.ts_settings = ts_settings 
        self.mipmap_options = mipmap_options

        # --- PRECOMPUTED ONCE PER RUN ---
        self.global_res_int = int(global_res)
        self._sorted_ts_names = sorted(self.ts_settings.keys(), key=len, reverse=True)
        self._compiled_rules = self._compile_rules()

    def _compile_rules(self):
        """Turn each texture set's map-name templates into compiled regexes once."""
        compiled = {}
        for ts_name, ts_data in self.ts_settings.items():
            rule_list = []
            for tmpl, rules in ts_data.get("rules", {}).items():
                safe_tmpl = re.escape(tmpl)
                regex_pattern = re.sub(r'\\\$[a-zA-Z0-9]+', '.*', safe_tmpl)
                regex_pattern = re.compile(f"^{regex_pattern}$", re.IGNORECASE)
                is_normal_tmpl = "norm" in tmpl.lower() or "_n" in tmpl.lower()
                rule_list.append((regex_pattern, rules, is_normal_tmpl))
            compiled[ts_name] = rule_list
        return compiled

    def _find_texture_set(self, base_name):
        for ts_name in self._sorted_ts_names:
            if ts_name in base_name:
                return ts_name
        return None

    def _process_one(self, file):
        """Resolve rules, resize if needed, and run nvcompress for a single exported file."""
        input_file = os.path.join(self.temp_dir, file)
        output_file = os.path.join(self.final_out_dir, file.replace(".png", ".dds"))
        base_name = os.path.splitext(file)[0]

        # --- IDENTIFY TEXTURE SET ---
        matching_ts = self._find_texture_set(base_name)

        if matching_ts:
            ts_data = self.ts_settings[matching_ts]
            comp_format = ts_data["fallback"]["comp"]
            target_res_str = ts_data["fallback"]["res"]
            compiled_rules = self._compiled_rules.get(matching_ts, [])
        else:
            comp_format = "Global"
            target_res_str = "Global"
            compiled_rules = []

        is_normal = False

        # --- DYNAMIC MATCHING ENGINE ---
        for regex_pattern, rules, is_normal_tmpl in compiled_rules:
            if regex_pattern.match(base_name):
                comp_format = rules["comp"]
                target_res_str = rules["res"]
                is_normal = is_normal_tmpl
                break

        if not is_normal and ("normal" in base_name.lower() or "_n" in base_name.lower()):
            is_normal = True

        # --- RESOLUTION & FORMAT RESIZING ENGINE ---
        target_res_int = self.global_res_int if target_res_str == "Global" else int(target_res_str)
        final_comp_format = self.global_format if comp_format == "Global" else comp_format

        if target_res_int != self.global_res_int:
            try:
                img = QtGui.QImage(input_file)
                if not img.isNull():
                    max_dim = max(img.width(), img.height())
                    if max_dim != target_res_int:
                        scale_factor = target_res_int / max_dim
                        new_w = max(1, int(img.width() * scale_factor))
                        new_h = max(1, int(img.height() * scale_factor))

                        scaled_img = img.scaled(new_w, new_h, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
                        scaled_img.save(input_file)
            except Exception as e:
                print(f"Warning: Failed to resize {file}. Error: {e}")

        # --- APPLY NVTT COMMANDS ---
        command = [self.exe_path, final_comp_format]

        if not self.mipmap_options["generate"]:
            command.append("-nomips")
        else:
            filter_flag = f"-{self.mipmap_options['filter'].lower()}"
            if filter_flag in ["-box", "-triangle", "-kaiser", "-mitchell"]:
                 command.append(filter_flag)

            if self.mipmap_options["min_size"] > 1:
                command.extend(["--min-mip-size", str(self.mipmap_options["min_size"])])

            if self.mipmap_options["max_count"] > 0:
                command.extend(["--max-mip-count", str(self.mipmap_options["max_count"])])

            if self.mipmap_options["gamma"]:
                command.append("-color")

            if self.mipmap_options["premult"]:
                command.append("-alpha")

        if is_normal:
            command.append("-normal")

        command.extend([input_file, output_file])

        subprocess.run(command, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return file

    def run(self):
        total_files = len(self.exported_files)
        if total_files == 0:
            self.finished_success.emit(0)
            return

        max_workers = min(8, max(1, os.cpu_count() or 4), total_files)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {executor.submit(self._process_one, f): f for f in self.exported_files}
                completed = 0
                for future in concurrent.futures.as_completed(future_to_file):
                    file = future_to_file[future]
                    try:
                        future.result()
                    except subprocess.CalledProcessError as e:
                        for pending in future_to_file:
                            pending.cancel()
                        self.finished_error.emit(f"Compression failed on {file}: {str(e)}")
                        return
                    except Exception as e:
                        for pending in future_to_file:
                            pending.cancel()
                        self.finished_error.emit(str(e))
                        return

                    completed += 1
                    progress_percent = int((completed / total_files) * 100)
                    self.progress_update.emit(progress_percent)

            self.finished_success.emit(total_files)
        except Exception as e:
            self.finished_error.emit(str(e))

class TextureSetWidget(QtWidgets.QWidget):
    """Custom Accordion-style widget for individual texture sets"""
    export_toggled = QtCore.Signal(str, bool)

    def __init__(self, ts_name, is_exported=True):
        super().__init__()
        self.ts_name = ts_name
        
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        self.header_layout = QtWidgets.QHBoxLayout()
        self.toggle_btn = QtWidgets.QToolButton()
        self.toggle_btn.setArrowType(QtCore.Qt.RightArrow)
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setStyleSheet("border: none; background: transparent; padding: 2px;")
        
        self.export_chk = QtWidgets.QCheckBox(ts_name)
        self.export_chk.setChecked(is_exported)
        
        self.header_layout.addWidget(self.toggle_btn)
        self.header_layout.addWidget(self.export_chk)
        self.header_layout.addStretch()
        
        self.content_widget = QtWidgets.QWidget()
        self.content_layout = QtWidgets.QGridLayout(self.content_widget)
        self.content_layout.setContentsMargins(30, 5, 5, 10) 
        self.content_widget.setVisible(False)
        
        layout.addLayout(self.header_layout)
        layout.addWidget(self.content_widget)
        
        self.toggle_btn.toggled.connect(self.on_toggle)
        self.export_chk.toggled.connect(lambda checked: self.export_toggled.emit(self.ts_name, checked))

    def on_toggle(self, checked):
        self.toggle_btn.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)
        self.content_widget.setVisible(checked)

class DDSExporterWidget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.is_loading_ui = True 
        self.temp_dir = None
        self.setWindowTitle("DDS Exporter")
        self.preset_data = {} 
        self.ts_settings = {} 
        self.ts_widgets = {} 

        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._write_state_to_disk)

        try:
            self.setup_ui()
            self.populate_presets()
            
            self.preset_combo.currentTextChanged.connect(self.update_rules_ui)
            if self.preset_combo.count() > 0:
                self.update_rules_ui(self.preset_combo.currentText())
                
            self.is_loading_ui = False
            
            if substance_painter.project.is_open():
                self.load_state()
                self.populate_texture_sets()
        except Exception as e:
            print(f"NVTT Init Error: {e}")

    def setup_ui(self):
        content_widget = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content_widget)
        content_layout.setContentsMargins(10, 10, 10, 10)
        
        dir_layout = QtWidgets.QHBoxLayout()
        self.dir_input = QtWidgets.QLineEdit()
        self.dir_input.setPlaceholderText("Select output directory...")
        self.dir_input.textChanged.connect(self.save_state) 
        self.dir_btn = QtWidgets.QPushButton("Browse")
        self.dir_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(self.dir_btn)
        content_layout.addLayout(dir_layout)

        grid_layout = QtWidgets.QGridLayout()
        grid_layout.setColumnStretch(0, 0)
        grid_layout.setColumnStretch(1, 1)
        
        grid_layout.addWidget(QtWidgets.QLabel("Export Preset:"), 0, 0)
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        grid_layout.addWidget(self.preset_combo, 0, 1)
        
        grid_layout.addWidget(QtWidgets.QLabel("Global Resolution:"), 1, 0)
        self.res_combo = QtWidgets.QComboBox()
        self.resolutions = {"8192": 13, "4096": 12, "2048": 11, "1024": 10, "512": 9, "256": 8}
        self.res_combo.addItems(list(self.resolutions.keys()))
        self.res_combo.setCurrentText("2048")
        self.res_combo.currentTextChanged.connect(self.save_state) 
        grid_layout.addWidget(self.res_combo, 1, 1)

        grid_layout.addWidget(QtWidgets.QLabel("Global Format:"), 2, 0)
        self.global_format_combo = QtWidgets.QComboBox()
        self.global_format_combo.addItems(["-bc1", "-bc3", "-bc5", "-bc7"])
        self.global_format_combo.setCurrentText("-bc7")
        self.global_format_combo.currentTextChanged.connect(self.save_state)
        grid_layout.addWidget(self.global_format_combo, 2, 1)

        content_layout.addLayout(grid_layout)
        
        self.ts_header_label = QtWidgets.QLabel("Texture Sets / Per-Map Rules")
        content_layout.addWidget(self.ts_header_label)
        
        self.ts_container_widget = QtWidgets.QWidget()
        self.ts_container_layout = QtWidgets.QVBoxLayout(self.ts_container_widget)
        self.ts_container_layout.setContentsMargins(0, 0, 0, 0)
        self.ts_container_layout.setSpacing(2)
        content_layout.addWidget(self.ts_container_widget)
        
        self.mipmap_group = QtWidgets.QGroupBox("Mipmap Options")
        self.mipmap_group.setCheckable(True) 
        self.mipmap_group.setChecked(True)
        self.mipmap_group.toggled.connect(self.save_state) 
        mipmap_layout = QtWidgets.QGridLayout()
        mipmap_layout.setColumnStretch(0, 1)
        mipmap_layout.setColumnStretch(1, 0)

        mipmap_layout.addWidget(QtWidgets.QLabel("Minimum Mipmap Size"), 0, 0)
        self.min_mip_spin = QtWidgets.QSpinBox()
        self.min_mip_spin.setRange(1, 8192)
        self.min_mip_spin.setValue(4)
        self.min_mip_spin.setSuffix(" px")
        self.min_mip_spin.valueChanged.connect(self.save_state) 
        mipmap_layout.addWidget(self.min_mip_spin, 0, 1)
        
        mipmap_layout.addWidget(QtWidgets.QLabel("Maximum Mipmap Count"), 1, 0)
        self.max_mip_spin = QtWidgets.QSpinBox()
        self.max_mip_spin.setRange(0, 20)
        self.max_mip_spin.setSpecialValueText("MAX") 
        self.max_mip_spin.setValue(0)
        self.max_mip_spin.valueChanged.connect(self.save_state) 
        mipmap_layout.addWidget(self.max_mip_spin, 1, 1)
        
        self.gamma_chk = QtWidgets.QCheckBox("Gamma Correct")
        self.gamma_chk.setChecked(True)
        self.gamma_chk.stateChanged.connect(self.save_state) 
        mipmap_layout.addWidget(self.gamma_chk, 2, 0, 1, 2)
        
        self.premult_chk = QtWidgets.QCheckBox("Premultiplied Alpha Blending")
        self.premult_chk.setChecked(True)
        self.premult_chk.stateChanged.connect(self.save_state) 
        mipmap_layout.addWidget(self.premult_chk, 3, 0, 1, 2)
        
        mipmap_layout.addWidget(QtWidgets.QLabel("Filter Type"), 4, 0)
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["Box", "Triangle", "Kaiser", "Mitchell"])
        self.filter_combo.setCurrentText("Mitchell")
        self.filter_combo.currentTextChanged.connect(self.save_state) 
        mipmap_layout.addWidget(self.filter_combo, 4, 1)
        self.mipmap_group.setLayout(mipmap_layout)
        content_layout.addWidget(self.mipmap_group)

        content_layout.addStretch()

        btn_v_layout = QtWidgets.QVBoxLayout()
        btn_v_layout.setSpacing(5)
        
        load_h_layout = QtWidgets.QHBoxLayout()
        self.load_btn = QtWidgets.QPushButton("Refresh Saved Settings")
        load_h_layout.addStretch()
        load_h_layout.addWidget(self.load_btn)
        load_h_layout.addStretch()
        
        export_h_layout = QtWidgets.QHBoxLayout()
        self.export_btn = QtWidgets.QPushButton("Export to DDS")
        export_h_layout.addStretch()
        export_h_layout.addWidget(self.export_btn)
        export_h_layout.addStretch()
        
        btn_v_layout.addLayout(load_h_layout)
        btn_v_layout.addLayout(export_h_layout)
        
        content_layout.addLayout(btn_v_layout)
        
        self.load_btn.clicked.connect(self.load_state)
        self.export_btn.clicked.connect(self.start_export_process)

        content_layout.addSpacing(10)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        content_layout.addWidget(self.progress_bar)

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame) 
        content_widget.setMinimumWidth(380) 
        scroll_area.setWidget(content_widget)
        
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll_area)

    def populate_texture_sets(self):
        if not substance_painter.project.is_open():
            return
            
        was_loading = getattr(self, 'is_loading_ui', False)
        self.is_loading_ui = True 
            
        for i in reversed(range(self.ts_container_layout.count())):
            w = self.ts_container_layout.itemAt(i).widget()
            if w: w.setParent(None)
        self.ts_widgets.clear()
        
        for ts in substance_painter.textureset.all_texture_sets():
            ts_name = ts.name()
            if ts_name not in self.ts_settings:
                self.ts_settings[ts_name] = {
                    "export": True, 
                    "rules": {}, 
                    "fallback": {"res": "Global", "comp": "Global"} 
                }
            
            is_exported = self.ts_settings[ts_name]["export"]
            ts_widget = TextureSetWidget(ts_name, is_exported)
            ts_widget.export_toggled.connect(self.on_ts_export_toggled)
            
            self.ts_container_layout.addWidget(ts_widget)
            self.ts_widgets[ts_name] = ts_widget
            
        if self.preset_combo.count() > 0:
            self.update_rules_ui(self.preset_combo.currentText())

        self.is_loading_ui = was_loading

    def on_ts_export_toggled(self, ts_name, checked):
        if self.is_loading_ui: return
        if ts_name in self.ts_settings:
            self.ts_settings[ts_name]["export"] = checked
            self.save_state()

    def populate_presets(self):
        self.preset_data.clear()
        self.preset_combo.clear()
        for shelf in substance_painter.resource.Shelves.all():
            export_presets_dir = f"{shelf.path()}/export-presets"
            if not os.path.isdir(export_presets_dir): continue
            for filename in os.listdir(export_presets_dir):
                if filename.endswith(".spexp"):
                    name = os.path.splitext(filename)[0]
                    filepath = os.path.join(export_presets_dir, filename)
                    preset_id = substance_painter.resource.ResourceID(context=shelf.name(), name=name)
                    self.preset_data[name] = {"url": preset_id.url(), "filepath": filepath}
                    self.preset_combo.addItem(name)

    def get_preset_maps(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                raw_bytes = f.read()
            clean_bytes = raw_bytes.replace(b'\x00', b'')
            raw_text = clean_bytes.decode('utf-8', 'ignore')
            maps = []
            maps.extend(re.findall(r'"fileName"\s*:\s*"([^"]+)"', raw_text))
            maps.extend(re.findall(r'<fileName>\s*([^<]+)\s*</fileName>', raw_text))
            maps.extend(re.findall(r'fileName\s*=\s*"([^"]+)"', raw_text))
            if not maps:
                maps.extend(re.findall(r'([a-zA-Z0-9_-]*\$textureSet[a-zA-Z0-9_-]*)', raw_text))
                maps.extend(re.findall(r'([a-zA-Z0-9_-]*\$project[a-zA-Z0-9_-]*)', raw_text))
                maps.extend(re.findall(r'([a-zA-Z0-9_-]*\$mesh[a-zA-Z0-9_-]*)', raw_text))
            seen = set()
            return [x for x in maps if not (x in seen or seen.add(x))]
        except Exception as e:
            return []

    def update_rules_ui(self, preset_name):
        was_loading = getattr(self, 'is_loading_ui', False)
        
        if not was_loading:
            for ts_name in self.ts_settings:
                self.ts_settings[ts_name]["rules"] = {}
                self.ts_settings[ts_name]["fallback"] = {"res": "Global", "comp": "Global"}
        
        self.is_loading_ui = True 
        
        filepath = self.preset_data.get(preset_name, {}).get("filepath")
        if not filepath:
            self.is_loading_ui = was_loading
            return
            
        map_templates = self.get_preset_maps(filepath)
        formats_pool = ["Global", "-bc1", "-bc3", "-bc5", "-bc7"] 
        res_pool = ["Global", "8192", "4096", "2048", "1024", "512", "256", "128", "64", "32"]

        for ts_name, widget in self.ts_widgets.items():
            layout = widget.content_layout
            
            for i in reversed(range(layout.count())):
                w = layout.itemAt(i).widget()
                if w: w.setParent(None)
                
            layout.addWidget(QtWidgets.QLabel("Map Output"), 0, 0)
            layout.addWidget(QtWidgets.QLabel("Target Size"), 0, 1)
            layout.addWidget(QtWidgets.QLabel("Format"), 0, 2)
            
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 0)
            layout.setColumnStretch(2, 0)
            
            ts_rules = self.ts_settings[ts_name].get("rules", {})
            ts_fallback = self.ts_settings[ts_name].get("fallback", {"res": "Global", "comp": "Global"})
            
            row = 1
            for tmpl in map_templates:
                if tmpl not in ts_rules:
                    ts_rules[tmpl] = {"res": "Global", "comp": "Global"}
                    
                rule = ts_rules[tmpl]
                
                lbl = QtWidgets.QLabel(tmpl)
                lbl.setWordWrap(True)
                
                res_combo = QtWidgets.QComboBox()
                res_combo.addItems(res_pool)
                res_combo.setCurrentText(rule["res"])
                res_combo.currentTextChanged.connect(lambda val, t_set=ts_name, t_tmpl=tmpl: self.on_rule_value_changed(t_set, t_tmpl, "res", val))
                
                comp_combo = QtWidgets.QComboBox()
                comp_combo.addItems(formats_pool)
                comp_combo.setCurrentText(rule["comp"])
                comp_combo.currentTextChanged.connect(lambda val, t_set=ts_name, t_tmpl=tmpl: self.on_rule_value_changed(t_set, t_tmpl, "comp", val))
                
                layout.addWidget(lbl, row, 0)
                layout.addWidget(res_combo, row, 1)
                layout.addWidget(comp_combo, row, 2)
                row += 1
                
            layout.addWidget(QtWidgets.QLabel("Fallback / Unknown:"), row, 0)
            
            fb_res = QtWidgets.QComboBox()
            fb_res.addItems(res_pool)
            fb_res.setCurrentText(ts_fallback["res"])
            fb_res.currentTextChanged.connect(lambda val, t_set=ts_name: self.on_fallback_value_changed(t_set, "res", val))
            
            fb_comp = QtWidgets.QComboBox()
            fb_comp.addItems(formats_pool)
            fb_comp.setCurrentText(ts_fallback["comp"])
            fb_comp.currentTextChanged.connect(lambda val, t_set=ts_name: self.on_fallback_value_changed(t_set, "comp", val))
            
            layout.addWidget(fb_res, row, 1)
            layout.addWidget(fb_comp, row, 2)
            
            self.ts_settings[ts_name]["rules"] = ts_rules

        self.is_loading_ui = was_loading
        if not self.is_loading_ui:
            self.save_state()

    def on_rule_value_changed(self, ts_name, tmpl, key, value):
        if self.is_loading_ui: return
        self.ts_settings[ts_name]["rules"][tmpl][key] = value
        self.save_state()

    def on_fallback_value_changed(self, ts_name, key, value):
        if self.is_loading_ui: return
        self.ts_settings[ts_name]["fallback"][key] = value
        self.save_state()

    def get_prefs_path(self):
        return os.path.join(os.path.expanduser('~'), 'Documents', 'SP_NVTT_Settings.json')

    def get_project_key(self):
        if not substance_painter.project.is_open(): return "UNSAVED_PROJECT"
        path = substance_painter.project.file_path()
        return path.replace("\\", "/").lower() if path else "UNSAVED_PROJECT"

    def save_state(self):
        if getattr(self, 'is_loading_ui', False): return
        self._save_timer.start()

    def _write_state_to_disk(self):
        state = {
            "output_dir": self.dir_input.text(),
            "preset": self.preset_combo.currentText(),
            "global_res": self.res_combo.currentText(),
            "global_format": self.global_format_combo.currentText(), 
            "ts_settings": self.ts_settings, 
            "mipmap": {
                "generate": self.mipmap_group.isChecked(),
                "min_size": self.min_mip_spin.value(),
                "max_count": self.max_mip_spin.value(),
                "gamma": self.gamma_chk.isChecked(),
                "premult": self.premult_chk.isChecked(),
                "filter": self.filter_combo.currentText()
            }
        }
        prefs_path = self.get_prefs_path()
        data = {}
        try:
            if os.path.exists(prefs_path):
                with open(prefs_path, 'r') as f: data = json.load(f)
        except Exception: pass
        data[self.get_project_key()] = state
        try:
            with open(prefs_path, 'w') as f: json.dump(data, f, indent=4)
        except Exception: pass

    def load_state(self):
        self.is_loading_ui = True 
        prefs_path = self.get_prefs_path()
        try:
            if os.path.exists(prefs_path):
                with open(prefs_path, 'r') as f: data = json.load(f)
                proj_key = self.get_project_key()
                state = data.get(proj_key, data.get(substance_painter.project.file_path() if substance_painter.project.is_open() else "", data.get("UNSAVED_PROJECT", {})))
                
                if "output_dir" in state: self.dir_input.setText(state["output_dir"])
                if "preset" in state:
                    self.preset_combo.blockSignals(True)
                    self.preset_combo.setCurrentText(state["preset"])
                    self.preset_combo.blockSignals(False)
                    self.update_rules_ui(state["preset"])
                if "global_res" in state: self.res_combo.setCurrentText(state["global_res"])
                if "global_format" in state: self.global_format_combo.setCurrentText(state["global_format"]) 
                
                if "ts_settings" in state:
                    self.ts_settings = state["ts_settings"]
                    
                if "mipmap" in state:
                    mip = state["mipmap"]
                    self.mipmap_group.setChecked(mip.get("generate", True))
                    self.min_mip_spin.setValue(mip.get("min_size", 4))
                    self.max_mip_spin.setValue(mip.get("max_count", 0))
                    self.gamma_chk.setChecked(mip.get("gamma", True))
                    self.premult_chk.setChecked(mip.get("premult", True))
                    self.filter_combo.setCurrentText(mip.get("filter", "Mitchell"))
        except Exception: pass
        self.is_loading_ui = False

    def browse_directory(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory: self.dir_input.setText(directory)

    def start_export_process(self):
        if not substance_painter.project.is_open():
            QtWidgets.QMessageBox.warning(self, "Error", "No project is currently open.")
            return
            
        final_out_dir = self.dir_input.text()
        if not final_out_dir or not os.path.exists(final_out_dir):
            QtWidgets.QMessageBox.warning(self, "Error", "Please select a valid output directory.")
            return
            
        if not os.path.exists(NVTT_EXECUTABLE):
            QtWidgets.QMessageBox.critical(self, "NVTT Missing", f"Could not find the bundled NVTT executable.\n\nPlease ensure your plugin folder contains the 'bin' folder with 'nvcompress.exe' here:\n\n{NVTT_EXECUTABLE}")
            return

        active_texture_sets = []
        for ts_name, widget in self.ts_widgets.items():
            if widget.export_chk.isChecked():
                active_texture_sets.append({"rootPath": ts_name})
                
        if not active_texture_sets:
            QtWidgets.QMessageBox.warning(self, "Error", "No texture sets are selected for export.")
            return

        self.export_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.temp_dir = tempfile.mkdtemp(prefix="sp_dds_export_").replace("\\", "/")
        
        target_res_log2 = self.resolutions[self.res_combo.currentText()]
        selected_preset_name = self.preset_combo.currentText()
        selected_preset_url = self.preset_data[selected_preset_name]["url"]
        
        export_config = {
            "exportShaderParams": False,
            "exportPath": self.temp_dir,
            "defaultExportPreset": selected_preset_url,
            "exportList": active_texture_sets,
            "exportParameters": [{"parameters": {"fileFormat": "png", "bitDepth": "8", "dithering": True, "paddingAlgorithm": "infinite", "sizeLog2": [target_res_log2, target_res_log2]}}]
        }
        
        try:
            substance_painter.export.export_project_textures(export_config)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Export Error", str(e))
            self.cleanup_and_reset()
            return
            
        exported_files = [f for f in os.listdir(self.temp_dir) if f.endswith(".png")]
        if not exported_files:
            QtWidgets.QMessageBox.information(self, "Info", "No textures were exported.")
            self.cleanup_and_reset()
            return
            
        self.worker = NVTTWorker(
            exported_files, 
            self.temp_dir, 
            final_out_dir, 
            NVTT_EXECUTABLE, 
            self.res_combo.currentText(),
            self.global_format_combo.currentText(), 
            self.ts_settings, 
            {
                "generate": self.mipmap_group.isChecked(), 
                "min_size": self.min_mip_spin.value(), 
                "max_count": self.max_mip_spin.value(), 
                "gamma": self.gamma_chk.isChecked(), 
                "premult": self.premult_chk.isChecked(), 
                "filter": self.filter_combo.currentText()
            }
        )
        self.worker.progress_update.connect(self.update_progress)
        self.worker.finished_success.connect(self.on_success)
        self.worker.finished_error.connect(self.on_error)
        self.worker.start()

    def update_progress(self, val): self.progress_bar.setValue(val)
    
    def on_success(self, count):
        QtWidgets.QMessageBox.information(self, "Success", f"Successfully exported  {count} textures.")
        self.cleanup_and_reset()
        
    def on_error(self, error_msg):
        QtWidgets.QMessageBox.critical(self, "Conversion Error", error_msg)
        self.cleanup_and_reset()
        
    def cleanup_and_reset(self):
        if self.temp_dir and os.path.exists(self.temp_dir): shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.export_btn.setEnabled(True)
        self.progress_bar.setValue(0)

# --- PLUGIN LIFECYCLE HOOKS ---
plugin_widget = None
project_opened_handler = None

def on_project_changed(event=None):
    if plugin_widget:
        plugin_widget.load_state()
        plugin_widget.populate_texture_sets()

def start_plugin():
    global plugin_widget, project_opened_handler
    plugin_widget = DDSExporterWidget()
    substance_painter.ui.add_dock_widget(plugin_widget)
    
    project_opened_handler = substance_painter.event.DISPATCHER.connect(
        substance_painter.event.ProjectOpened, on_project_changed
    )

def close_plugin():
    global plugin_widget, project_opened_handler
    if project_opened_handler:
        substance_painter.event.DISPATCHER.disconnect(substance_painter.event.ProjectOpened, project_opened_handler)
        project_opened_handler = None
    if plugin_widget:
        substance_painter.ui.delete_ui_element(plugin_widget)
        plugin_widget = None

if __name__ == "__main__":
    start_plugin()