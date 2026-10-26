# Colour Key Editor

A small Windows GUI for cleaning the transparent background of Helbreath sprite sheets and authoring each
sheet's colour key, using [PAKLib](https://github.com/shadowevil/PAKLib) 2.0.

Why: the client takes the transparent colour from each sheet's top-left pixel and keys out exactly that colour
(after converting to 16-bit RGB565). Many original sheets have background pixels that drift from that colour, or
leftover art outside the frames, which can show as artifacts. PAKLib 2.0 stores an authored key per sheet in the
4 bytes before the bitmap (`FF RR GG BB`).

## Use

```
ColorKeyEditor.exe [file.pak | folder] ...
```

- **Open PAK files** (Ctrl+O, multi-select), **Open Folder** (Ctrl+Shift+O: every `.pak` in the folder and its
  subfolders, as a folder tree), or drag files and folders onto the window. Many paks can be open at once.
- The tree on the left lists each pak (loaded when expanded; `*` = unsaved) and its sheets: size, frame count, key
  (`K` = authored, otherwise the first pixel once the sheet is viewed; `------` before), and `drift N` = pixels outside the frames that are not the key.
  `*` marks a changed sheet.
- **Canvas**: mouse wheel zooms around the cursor; middle or right drag, or Space + left drag, pans; `F` fits,
  `1` is actual size, `+`/`-` zoom. A pixel grid appears from 1200%.
- **Hotkeys** (listed bottom-right of the canvas; ignored while typing in a field): `K` sets the key to the first
  pixel, `C` clears outside the frames to the key, `T` toggles the transparency key view. `K` then `C` cleans a
  sheet.
- **Transparency key view** shows the sheet the way the client's `PutSpriteFast` draws it, with no blending: key
  pixels become a checkerboard. With *Match the key as the client does (RGB565)* on (the default), every pixel with
  the key's 16-bit value counts as key, exactly as in game; off, only the exact 24-bit key colour does. Selected
  colours show in magenta, so drift pixels
  stand out. Frames are outlined in cyan.
- **Canvas click selects colour**: click a pixel to select or deselect its colour, drag to add every colour swept
  over, Ctrl+drag to remove them. The colour list follows. **Canvas click picks key colour**: click to make it the
  key. Or pick the key with *First pixel* / *Choose...*.
- Double-click a colour in the list to centre the canvas on its first pixel.
- The colour list shows every colour, nearest to the key first; click a row to tick or untick it:
  distance (largest channel difference),
  whether it is already the key in RGB565 (`565 = key`, so already transparent in game), and how many pixels are
  outside and inside frames. *Select within N* selects every colour within distance N.
- **Replace selected colours with key** recolours every pixel of those colours on the sheet, inside frames too.
- **Clear outside frames to key** fills everything no frame covers with the key.
- **PAK > Clear outside frames on every sheet** does that for all sheets of the current pak with key = first
  pixel, skipping sheets whose first pixel is inside a frame.
- **Revert sheet** restores the sheet's pixels and key as opened or last saved. **File > Save PAK** (Ctrl+S) writes
  the current pak, **Save All** (Ctrl+Shift+S) every changed one: changed sheets as 24bpp BMPs, and the key for
  every sheet whose key was set or edited.

Check a sheet before clearing outside its frames: some sheets hold art there that no frame uses (for example the
640x480 sheets of `Tile462-464.pak`), and clearing removes it.

PAKLib 2.0 saves every bitmap as 24bpp, so 8bpp and 1bpp sheets are converted on the first save.

## Build

Requires the .NET 8 SDK or later (Windows). Open `ColorKeyEditor.sln` in Visual Studio, or:

```
dotnet build ColorKeyEditor.sln -c Release
```

It uses the NuGet `PAKLib` 2.0.1 package (2.0.0 shipped the old 1.1 DLL; skip it). Until 2.0.1 is on NuGet, or to
build against a PAKLib checkout:

```
dotnet build -c Release -p:PAKLibSource=C:\path\to\PAKLib\PAKLib.csproj
```
