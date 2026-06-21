# DDS Exporter for Substance 3D Painter

A powerful, multithreaded plugin that bridges the gap between Substance 3D Painter and the NVIDIA Texture Tools. This addon automates the export and compression of textures directly into production-ready `.dds` files, saving you from the tedious process of manual conversion in external software. 

<img width="385" height="654" alt="image" src="https://github.com/user-attachments/assets/e5cf5a0a-7473-4f30-a501-f41f46a492bb" />

## ✨ Key Features

* 📂 **Dynamic Export Preset Integration** The plugin automatically reads your saved Substance Painter Export Presets (`.spexp`). The UI dynamically updates to list exactly the maps defined in your preset, allowing you to assign rules perfectly matched to your active workflow.
* 🚀 **Multithreaded Performance** Utilizes Python's `ThreadPoolExecutor` to bypass the Global Interpreter Lock (GIL). By processing multiple textures simultaneously across your CPU cores, conversion times are drastically reduced.
* 🎯 **Granular Per-Map Optimization** Don't settle for one-size-fits-all compression. Assign specific DDS formats (e.g., `-bc1`, `-bc3`, `-bc5`, `-bc7`) and target resolutions to individual maps. 
* 🗂️ **Texture Set Filtering** A clean, native-feeling Accordion UI allows you to easily toggle which Texture Sets you want to export and convert, saving time when iterating on specific parts of a model.
* 📐 **Smart Resolution Scaling** Need a 512px output but working in 4K? The plugin intercepts the exported PNGs and scales them down safely in memory *before* feeding them to the NVTT compiler, saving massive amounts of processing time.
* 🖼️ **Advanced Mipmap Control** Full access to NVIDIA's mipmap generation settings. Control Minimum Mipmap Size, Maximum Mipmap Count, filter types (Box, Triangle, Kaiser, Mitchell), Gamma Correction, and Premultiplied Alpha blending.
* 💾 **Persistent Project Settings** Your export configurations and preset choices are saved automatically per-project using a debounced background timer. Close your software and come back tomorrow—your custom compression rules will be exactly as you left them.
* 📦 **Plug-and-Play Architecture** Designed to be distributed with the NVIDIA Texture Tools bundled directly inside the plugin folder. Users can simply drop the folder into their plugins directory and start exporting immediately, with no external software installation required.

## 🛠️ How to Install the DDS Exporter

### Step 1: Locate your Plugins Folder
The easiest way to find your plugins folder is directly through Substance Painter:
1. Launch **Adobe Substance 3D Painter**.
2. At the top of the window, click on the **Python** menu.
3. Select **Plugins folder**. This will automatically open the correct directory in your File Explorer!


### Step 2: Extract DDS Exporter to Plugin Folder
Extract the downloaded **DDS_Exporter** folder directly into the `plugins` directory that just opened in Step 1. 

To ensure the tool works correctly, verify that your folder structure looks exactly like this:

plugins/
└── DDS_Exporter/
    ├── __init__.py           
    └── bin/                  
        ├── FreeImage.dll    
        ├── nvcompress.exe          
        ├── nvtt30205.dll        
        └── vcomp140.dll

### Step 3: Enable the Plugin in Substance Painter
<img width="161" height="101" alt="image" src="https://github.com/user-attachments/assets/22928a02-134a-4a97-9b7c-6a964786d114" />


1. Launch **Adobe Substance 3D Painter**.
2. At the top of the window, click on the **Python** menu.
3. Select **Plugins**.
4. Look for `DDS_Exporter` in the list and check the box next to it to enable it.
5. The **DDS Exporter** panel will appear. You can now dock it anywhere in your UI!
