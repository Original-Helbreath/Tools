using PAKLib;

namespace ColorKeyEditor;

public sealed class MainForm : Form
{
    private PAK? pak;
    private string? pakPath;
    private readonly List<SheetImage?> sheets = new();   // decoded on first use
    private SheetImage? current;
    private int currentIndex = -1;
    private bool dirty;
    private bool syncing;

    private readonly Font listFont = new("Consolas", 9f);
    private readonly ListBox sheetList = new() { Dock = DockStyle.Fill, IntegralHeight = false };
    private readonly Panel viewPanel = new() { Dock = DockStyle.Fill, AutoScroll = true, BackColor = Color.FromArgb(40, 40, 40) };
    private readonly SheetView view = new() { Location = new Point(0, 0) };
    private readonly Panel keySwatch = new() { Size = new Size(40, 22), BorderStyle = BorderStyle.FixedSingle };
    private readonly Label keyLabel = new() { AutoSize = true };
    private readonly CheckBox keyViewBox = new() { Text = "Transparency key view", AutoSize = true };
    private readonly CheckBox framesBox = new() { Text = "Show frames", AutoSize = true, Checked = true };
    private readonly NumericUpDown zoomBox = new() { Minimum = 1, Maximum = 16, Value = 2, Width = 50 };
    private readonly RadioButton modeDrift = new() { Text = "Click toggles drift colour", AutoSize = true, Checked = true };
    private readonly RadioButton modeKey = new() { Text = "Click picks key colour", AutoSize = true };
    private readonly NumericUpDown distanceBox = new() { Minimum = 0, Maximum = 255, Value = 24, Width = 55 };
    private readonly ListView colorList = new()
    {
        View = View.Details, CheckBoxes = true, FullRowSelect = true, Dock = DockStyle.Fill, HideSelection = false,
    };
    private readonly ToolStripStatusLabel status = new() { Spring = true, TextAlign = ContentAlignment.MiddleLeft };
    private readonly ToolStripStatusLabel hover = new() { AutoSize = false, Width = 380, TextAlign = ContentAlignment.MiddleLeft };
    private List<ColorStat> stats = new();

    public MainForm(string? openPath)
    {
        Text = "Colour Key Editor";
        Size = new Size(1400, 900);
        StartPosition = FormStartPosition.CenterScreen;
        KeyPreview = true;
        sheetList.Font = listFont;
        BuildLayout();
        if (openPath != null) Shown += (_, _) => OpenPak(Path.GetFullPath(openPath));
    }

    // ---------- layout ----------

    private void BuildLayout()
    {
        var menu = new MenuStrip();
        var file = new ToolStripMenuItem("&File");
        file.DropDownItems.Add(new ToolStripMenuItem("&Open PAK...", null, (_, _) => OpenDialog(), Keys.Control | Keys.O));
        file.DropDownItems.Add(new ToolStripMenuItem("&Save", null, (_, _) => Save(pakPath), Keys.Control | Keys.S));
        file.DropDownItems.Add("Save &As...", null, (_, _) => SaveAs());
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add("E&xit", null, (_, _) => Close());
        var pakMenu = new ToolStripMenuItem("&PAK");
        pakMenu.DropDownItems.Add("Clear outside frames on every sheet (key = first pixel)...", null, (_, _) => ClearAllSheets());
        menu.Items.Add(file);
        menu.Items.Add(pakMenu);

        var statusStrip = new StatusStrip();
        statusStrip.Items.Add(status);
        statusStrip.Items.Add(hover);

        // right-hand panel
        var side = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, Padding = new Padding(6) };
        side.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        side.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        side.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        side.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        side.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var keyGroup = new GroupBox { Text = "Transparency key", Dock = DockStyle.Top, AutoSize = true };
        var keyFlow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, WrapContents = true };
        var firstPixel = new Button { Text = "First pixel", AutoSize = true };
        var chooseKey = new Button { Text = "Choose...", AutoSize = true };
        firstPixel.Click += (_, _) => { if (current != null) SetKey(current.FirstPixel); };
        chooseKey.Click += (_, _) => ChooseKey();
        keyFlow.Controls.AddRange(new Control[] { keySwatch, keyLabel, firstPixel, chooseKey });
        keyGroup.Controls.Add(keyFlow);

        var viewGroup = new GroupBox { Text = "View", Dock = DockStyle.Top, AutoSize = true };
        var viewFlow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.TopDown };
        var zoomRow = new FlowLayoutPanel { AutoSize = true };
        zoomRow.Controls.Add(new Label { Text = "Zoom", AutoSize = true, Margin = new Padding(0, 6, 0, 0) });
        zoomRow.Controls.Add(zoomBox);
        viewFlow.Controls.AddRange(new Control[] { keyViewBox, framesBox, zoomRow, modeDrift, modeKey });
        viewGroup.Controls.Add(viewFlow);

        var driftRow = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true };
        var selectNear = new Button { Text = "Select within", AutoSize = true };
        var selectNone = new Button { Text = "Select none", AutoSize = true };
        selectNear.Click += (_, _) => SelectWithinDistance();
        selectNone.Click += (_, _) => SetAllChecks(false);
        driftRow.Controls.AddRange(new Control[] { selectNear, distanceBox, new Label { Text = "of key", AutoSize = true, Margin = new Padding(0, 6, 0, 0) }, selectNone });

        colorList.Columns.Add("Colour", 90);
        colorList.Columns.Add("Dist", 45, HorizontalAlignment.Right);
        colorList.Columns.Add("565 = key", 70);
        colorList.Columns.Add("Outside", 65, HorizontalAlignment.Right);
        colorList.Columns.Add("Inside", 65, HorizontalAlignment.Right);
        colorList.ItemChecked += (_, e) => OnColorChecked(e.Item);

        var actions = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown };
        var replace = new Button { Text = "Replace selected colours with key", AutoSize = true };
        var clear = new Button { Text = "Clear outside frames to key", AutoSize = true };
        var revert = new Button { Text = "Revert sheet", AutoSize = true };
        replace.Click += (_, _) => ReplaceSelected();
        clear.Click += (_, _) => ClearOutside();
        revert.Click += (_, _) => RevertSheet();
        actions.Controls.AddRange(new Control[] { replace, clear, revert });

        side.Controls.Add(keyGroup, 0, 0);
        side.Controls.Add(viewGroup, 0, 1);
        side.Controls.Add(driftRow, 0, 2);
        side.Controls.Add(colorList, 0, 3);
        side.Controls.Add(actions, 0, 4);

        viewPanel.Controls.Add(view);

        var inner = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel2 };
        inner.Panel1.Controls.Add(viewPanel);
        inner.Panel2.Controls.Add(side);
        var outer = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel1 };
        outer.Panel1.Controls.Add(sheetList);
        outer.Panel2.Controls.Add(inner);

        Controls.Add(outer);
        Controls.Add(statusStrip);
        Controls.Add(menu);
        MainMenuStrip = menu;

        Load += (_, _) =>
        {
            outer.SplitterDistance = 300;
            inner.SplitterDistance = Math.Max(200, inner.Width - 380);
        };

        sheetList.SelectedIndexChanged += (_, _) => ShowSheet(sheetList.SelectedIndex);
        keyViewBox.CheckedChanged += (_, _) => { view.KeyView = keyViewBox.Checked; view.Rebuild(); };
        framesBox.CheckedChanged += (_, _) => { view.ShowFrames = framesBox.Checked; view.Invalidate(); };
        zoomBox.ValueChanged += (_, _) => view.SetZoom((int)zoomBox.Value);
        view.SetZoom((int)zoomBox.Value);
        view.PixelHovered += OnHover;
        view.PixelClicked += OnPixelClicked;
        FormClosing += (_, e) => { if (!ConfirmDiscard()) e.Cancel = true; };
    }

    // ---------- file ----------

    private void OpenDialog()
    {
        if (!ConfirmDiscard()) return;
        using var dlg = new OpenFileDialog { Filter = "PAK files (*.pak)|*.pak|All files|*.*" };
        if (dlg.ShowDialog(this) == DialogResult.OK) OpenPak(dlg.FileName);
    }

    private void OpenPak(string path)
    {
        try
        {
            var loaded = PAK.ReadFromFile(path);
            if (loaded.Data == null) throw new InvalidDataException("No PAK data.");
            pak = loaded;
            pakPath = path;
            sheets.Clear();
            sheets.AddRange(Enumerable.Repeat<SheetImage?>(null, loaded.Data.Sprites.Count));
            current = null;
            currentIndex = -1;
            dirty = false;
            RefreshSheetList();
            Text = "Colour Key Editor - " + Path.GetFileName(path);
            if (sheets.Count > 0) sheetList.SelectedIndex = 0; else ShowSheet(-1);
            status.Text = $"Opened {path}: {sheets.Count} sheets.";
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Cannot open PAK", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void SaveAs()
    {
        if (pak == null) return;
        using var dlg = new SaveFileDialog { Filter = "PAK files (*.pak)|*.pak", FileName = Path.GetFileName(pakPath) };
        if (dlg.ShowDialog(this) == DialogResult.OK) Save(dlg.FileName);
    }

    private void Save(string? path)
    {
        if (pak?.Data == null || path == null) return;
        try
        {
            for (int i = 0; i < sheets.Count; i++)
            {
                var s = sheets[i];
                if (s == null) continue;   // never opened: written as loaded
                var sprite = pak.Data.Sprites[i];
                if (s.Modified) sprite.data = s.ToBmp24();
                if (s.KeyChanged)
                {
                    if (s.KeyAuthored) sprite.SetColorKey((byte)(s.Key >> 16), (byte)(s.Key >> 8), (byte)s.Key);
                    else sprite.ClearColorKey();
                }
            }
            PAK.SaveToFile(pak, path);
            foreach (var s in sheets) s?.MarkSaved();
            RefreshSheetList();
            pakPath = path;
            dirty = false;
            Text = "Colour Key Editor - " + Path.GetFileName(path);
            status.Text = "Saved " + path;
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Cannot save PAK", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private bool ConfirmDiscard()
    {
        if (!dirty) return true;
        return MessageBox.Show(this, "Discard unsaved changes?", "Colour Key Editor", MessageBoxButtons.YesNo,
            MessageBoxIcon.Warning) == DialogResult.Yes;
    }

    // ---------- sheets ----------

    private SheetImage? Sheet(int i)
    {
        if (i < 0 || i >= sheets.Count) return null;
        try { return sheets[i] ??= SheetImage.FromSprite(pak!.Data!.Sprites[i]); }
        catch (Exception ex)
        {
            status.Text = $"Sheet {i} cannot be decoded: {ex.Message}";
            return null;
        }
    }

    private string Describe(int i)
    {
        var sprite = pak!.Data!.Sprites[i];
        var s = sheets[i];
        if (s == null)
        {
            SpriteImage.TryGetSize(sprite.data, out int w, out int h);
            string k = sprite.ColorKey.HasValue ? "K " + (sprite.ColorKey.Value & 0xFFFFFF).ToString("X6") : "  ------";
            return $" {i,3} {w,4}x{h,-4} {sprite.Rectangles.Count,3}f {k}";
        }
        string key = (s.KeyAuthored ? "K " : "  ") + s.Key.ToString("X6");
        string mark = s.Modified || s.KeyChanged ? "*" : " ";
        return $"{mark}{i,3} {s.Width,4}x{s.Height,-4} {sprite.Rectangles.Count,3}f {key} {(s.DriftOutside > 0 ? "drift " + s.DriftOutside : "")}";
    }

    private void RefreshSheetList()
    {
        int sel = sheetList.SelectedIndex;
        syncing = true;
        try
        {
            sheetList.BeginUpdate();
            sheetList.Items.Clear();
            for (int i = 0; i < sheets.Count; i++) sheetList.Items.Add(Describe(i));
            sheetList.EndUpdate();
            if (sel >= 0 && sel < sheets.Count) sheetList.SelectedIndex = sel;
        }
        finally { syncing = false; }
    }

    private void RefreshSheetRow(int i)
    {
        if (i < 0 || i >= sheetList.Items.Count) return;
        syncing = true;
        try { sheetList.Items[i] = Describe(i); }
        finally { syncing = false; }
    }

    private void ShowSheet(int i)
    {
        if (syncing || i == currentIndex && current != null && i >= 0) return;
        currentIndex = i;
        current = Sheet(i);
        view.Highlight.Clear();
        RefreshSheetRow(i);
        var rects = current == null
            ? Enumerable.Empty<Rectangle>()
            : pak!.Data!.Sprites[i].Rectangles.Select(r => new Rectangle(r.x, r.y, r.width, r.height));
        view.SetSheet(current, rects);
        RefreshKey();
        RefreshColors();
    }

    private void Changed()
    {
        dirty = true;
        view.Rebuild();
        RefreshKey();
        RefreshColors();
        RefreshSheetRow(currentIndex);
    }

    // ---------- key ----------

    private void RefreshKey()
    {
        if (current == null) { keySwatch.BackColor = SystemColors.Control; keyLabel.Text = ""; return; }
        keySwatch.BackColor = Color.FromArgb(current.Key | unchecked((int)0xFF000000));
        keyLabel.Text = $"{current.Key:X6}  {(current.KeyAuthored ? "authored" : "first pixel")}";
    }

    private void SetKey(int key)
    {
        if (current == null) return;
        current.Key = key & 0xFFFFFF;
        current.KeyAuthored = true;
        view.Highlight.Clear();
        Changed();
    }

    private void ChooseKey()
    {
        if (current == null) return;
        using var dlg = new ColorDialog { Color = Color.FromArgb(current.Key | unchecked((int)0xFF000000)), FullOpen = true };
        if (dlg.ShowDialog(this) == DialogResult.OK) SetKey(dlg.Color.ToArgb());
    }

    // ---------- drift colours ----------

    private void RefreshColors()
    {
        syncing = true;
        colorList.BeginUpdate();
        try { FillColorList(); }
        finally { colorList.EndUpdate(); syncing = false; }
    }

    private void FillColorList()
    {
        colorList.Items.Clear();
        stats = current?.CountColors().OrderBy(c => c.Distance).ThenByDescending(c => c.Outside).ToList() ?? new();
        // a sheet can hold tens of thousands of colours; list the nearest ones, which is where drift lives,
        // plus any selected colour further away
        foreach (var c in stats.Take(2000).Concat(stats.Skip(2000).Where(c => view.Highlight.Contains(c.Color))))
        {
            var item = new ListViewItem($"{c.Color:X6}") { Tag = c.Color, UseItemStyleForSubItems = false };
            item.BackColor = Color.FromArgb(c.Color | unchecked((int)0xFF000000));
            item.ForeColor = (c.Color >> 16 & 0xFF) * 3 + (c.Color >> 8 & 0xFF) * 6 + (c.Color & 0xFF) > 1280 ? Color.Black : Color.White;
            item.SubItems.Add(c.Distance.ToString());
            item.SubItems.Add(c.SameIn565 ? "yes" : "");
            item.SubItems.Add(c.Outside.ToString());
            item.SubItems.Add(c.Inside.ToString());
            item.Checked = view.Highlight.Contains(c.Color);
            colorList.Items.Add(item);
        }
    }

    private void OnColorChecked(ListViewItem item)
    {
        if (syncing || item.Tag is not int color) return;
        if (item.Checked) view.Highlight.Add(color); else view.Highlight.Remove(color);
        if (view.KeyView) view.Rebuild();
    }

    private void ToggleColor(int color)
    {
        if (current == null || color == current.Key) return;
        if (!view.Highlight.Add(color)) view.Highlight.Remove(color);
        bool listed = false;
        syncing = true;
        try
        {
            foreach (ListViewItem item in colorList.Items)
                if (item.Tag is int c && c == color) { item.Checked = view.Highlight.Contains(color); item.EnsureVisible(); listed = true; }
        }
        finally { syncing = false; }
        if (!listed) RefreshColors();
        if (view.KeyView) view.Rebuild();
    }

    private void SelectWithinDistance()
    {
        if (current == null) return;
        int max = (int)distanceBox.Value;
        foreach (var c in stats)
            if (c.Distance <= max) view.Highlight.Add(c.Color);
        RefreshColors();
        view.Rebuild();
    }

    private void SetAllChecks(bool on)
    {
        if (!on) view.Highlight.Clear();
        RefreshColors();
        view.Rebuild();
    }

    private void ReplaceSelected()
    {
        if (current == null || view.Highlight.Count == 0) return;
        int n = current.ReplaceWithKey(view.Highlight);
        view.Highlight.Clear();
        if (n > 0) current.KeyAuthored = true;
        Changed();
        status.Text = $"Replaced {n} pixels with the key.";
    }

    private void ClearOutside()
    {
        if (current == null) return;
        int n = current.ClearOutsideFrames();
        if (n > 0) current.KeyAuthored = true;
        Changed();
        status.Text = $"Cleared {n} pixels outside the frames.";
    }

    private void RevertSheet()
    {
        if (current == null) return;
        current.Revert();
        view.Highlight.Clear();
        Changed();
        status.Text = "Sheet reverted to its pixels and key as opened or last saved.";
    }

    private void ClearAllSheets()
    {
        if (sheets.Count == 0) return;
        if (MessageBox.Show(this,
                "For every sheet: set the key to its first pixel and clear everything outside the frames to it.\n" +
                "This replaces any key already authored on those sheets.\n" +
                "Sheets whose first pixel lies inside a frame are skipped.", "Clear all sheets",
                MessageBoxButtons.OKCancel) != DialogResult.OK) return;
        int done = 0, skipped = 0;
        long pixels = 0;
        Cursor = Cursors.WaitCursor;
        for (int i = 0; i < sheets.Count; i++)
        {
            var s = Sheet(i);
            if (s == null || s.InFrame.Length == 0 || s.InFrame[0]) { skipped++; continue; }
            if (s.Key != s.FirstPixel) { s.Key = s.FirstPixel; s.KeyAuthored = true; }
            int n = s.ClearOutsideFrames();
            if (n > 0) s.KeyAuthored = true;
            pixels += n;
            done++;
        }
        Cursor = Cursors.Default;
        dirty = true;
        view.Highlight.Clear();
        view.Rebuild();
        RefreshKey();
        RefreshColors();
        RefreshSheetList();
        status.Text = $"Cleared {pixels} pixels on {done} sheets; skipped {skipped} whose first pixel is inside a frame.";
    }

    // ---------- mouse ----------

    private void OnHover(int x, int y)
    {
        if (current == null || x < 0) { hover.Text = ""; return; }
        int i = y * current.Width + x;
        int c = current.Pixels[i];
        string what = c == current.Key ? "key" : $"dist {SheetImage.Distance(c, current.Key)}";
        hover.Text = $"({x}, {y})  {c:X6}  {what}  {(current.InFrame[i] ? "inside frame" : "outside frames")}";
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) listFont.Dispose();
        base.Dispose(disposing);
    }

    private void OnPixelClicked(int x, int y, MouseButtons button)
    {
        if (current == null || button != MouseButtons.Left) return;
        int c = current.Pixels[y * current.Width + x];
        if (modeKey.Checked) SetKey(c); else ToggleColor(c);
    }
}
