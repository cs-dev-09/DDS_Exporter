# DDS_Exporter



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
1. Launch **Adobe Substance 3D Painter**.
2. At the top of the window, click on the **Python** menu.
3. Select **Plugins**.
4. Look for `DDS_Exporter` in the list and check the box next to it to enable it.
5. The **DDS Exporter** panel will appear. You can now dock it anywhere in your UI!