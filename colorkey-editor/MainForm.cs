namespace ColorKeyEditor;

public sealed class MainForm : Form
{
    private sealed record SheetRef(PakDocument Doc, int Index);

    private readonly List<PakDocument> docs = new();
    private PakDocument? currentDoc;   // pak selected in the tree (Save, Close, PAK menu)
    private PakDocument? sheetDoc;     // pak that owns the sheet on the canvas
    private SheetImage? current;
    private int currentIndex = -1;
    private bool syncing;
    private List<ColorStat> stats = new();

    private readonly Font treeFont = new("Consolas", 9f);
    private readonly TreeView tree = new() { Dock = DockStyle.Fill, HideSelection = false };
    private readonly SheetView view = new() { Dock = DockStyle.Fill };
    private readonly Panel keySwatch = new() { Size = new Size(40, 22), BorderStyle = BorderStyle.FixedSingle };
    private readonly Label keyLabel = new() { AutoSize = true, Margin = new Padding(3, 6, 3, 0) };
    private readonly CheckBox keyViewBox = new() { Text = "Transparency key view", AutoSize = true };
    private readonly CheckBox framesBox = new() { Text = "Show frames", AutoSize = true, Checked = true };
    private readonly RadioButton modeDrift = new() { Text = "Canvas click selects colour", AutoSize = true, Checked = true };
    private readonly RadioButton modeKey = new() { Text = "Canvas click picks key colour", AutoSize = true };
    private readonly NumericUpDown distanceBox = new() { Minimum = 0, Maximum = 255, Value = 24, Width = 55 };
    private readonly Label selectedLabel = new() { AutoSize = true, Margin = new Padding(3, 6, 3, 0) };
    private readonly ListView colorList = new()
    {
        View = View.Details, CheckBoxes = true, FullRowSelect = true, Dock = DockStyle.Fill, HideSelection = false,
    };
    private readonly ToolStripStatusLabel status = new() { Spring = true, TextAlign = ContentAlignment.MiddleLeft };
    private readonly ToolStripStatusLabel hover = new() { AutoSize = false, Width = 360, TextAlign = ContentAlignment.MiddleLeft };
    private readonly ToolStripStatusLabel zoomLabel = new() { AutoSize = false, Width = 70 };

    public MainForm(string[] openPaths)
    {
        Text = "Colour Key Editor";
        Size = new Size(1500, 950);
        StartPosition = FormStartPosition.CenterScreen;
        AllowDrop = true;
        tree.Font = treeFont;
        BuildLayout();
        if (openPaths.Length > 0) Shown += (_, _) => OpenPaths(openPaths);
    }

    // ---------- layout ----------

    private void BuildLayout()
    {
        var menu = new MenuStrip();
        var file = new ToolStripMenuItem("&File");
        file.DropDownItems.Add(new ToolStripMenuItem("&Open PAK files...", null, (_, _) => OpenFilesDialog(), Keys.Control | Keys.O));
        file.DropDownItems.Add(new ToolStripMenuItem("Open &Folder...", null, (_, _) => OpenFolderDialog(), Keys.Control | Keys.Shift | Keys.O));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add(new ToolStripMenuItem("&Save PAK", null, (_, _) => SaveDoc(currentDoc, null), Keys.Control | Keys.S));
        file.DropDownItems.Add(new ToolStripMenuItem("Save PAK &As...", null, (_, _) => SaveAs()));
        file.DropDownItems.Add(new ToolStripMenuItem("Save A&ll", null, (_, _) => SaveAll(), Keys.Control | Keys.Shift | Keys.S));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add(new ToolStripMenuItem("&Close PAK", null, (_, _) => CloseDoc(currentDoc)));
        file.DropDownItems.Add(new ToolStripMenuItem("Close A&ll", null, (_, _) => CloseAll()));
        file.DropDownItems.Add(new ToolStripSeparator());
        file.DropDownItems.Add(new ToolStripMenuItem("E&xit", null, (_, _) => Close()));

        var viewMenu = new ToolStripMenuItem("&View");
        viewMenu.DropDownItems.Add(new ToolStripMenuItem("&Fit to window (F)", null, (_, _) => view.FitToView()));
        viewMenu.DropDownItems.Add(new ToolStripMenuItem("&Actual size (1)", null, (_, _) => view.ActualSize()));
        viewMenu.DropDownItems.Add(new ToolStripMenuItem("Zoom &in (+)", null, (_, _) => view.ZoomBy(1.25f)));
        viewMenu.DropDownItems.Add(new ToolStripMenuItem("Zoom &out (-)", null, (_, _) => view.ZoomBy(0.8f)));

        var pakMenu = new ToolStripMenuItem("&PAK");
        pakMenu.DropDownItems.Add("Clear outside frames on every sheet of this PAK (key = first pixel)...", null, (_, _) => ClearAllSheets());

        menu.Items.AddRange(new ToolStripItem[] { file, viewMenu, pakMenu });

        var statusStrip = new StatusStrip();
        statusStrip.Items.AddRange(new ToolStripItem[] { status, hover, zoomLabel });

        // right-hand panel
        var side = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, Padding = new Padding(6) };
        for (int i = 0; i < 4; i++) side.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        side.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        side.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        var keyGroup = new GroupBox { Text = "Transparency key", Dock = DockStyle.Top, AutoSize = true };
        var keyFlow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true };
        var firstPixel = new Button { Text = "First pixel", AutoSize = true };
        var chooseKey = new Button { Text = "Choose...", AutoSize = true };
        firstPixel.Click += (_, _) => { if (current != null) SetKey(current.FirstPixel); };
        chooseKey.Click += (_, _) => ChooseKey();
        keyFlow.Controls.AddRange(new Control[] { keySwatch, keyLabel, firstPixel, chooseKey });
        keyGroup.Controls.Add(keyFlow);

        var viewGroup = new GroupBox { Text = "Canvas", Dock = DockStyle.Top, AutoSize = true };
        var viewFlow = new FlowLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, FlowDirection = FlowDirection.TopDown };
        var help = new Label
        {
            AutoSize = true, ForeColor = SystemColors.GrayText,
            Text = "Wheel: zoom   Middle/right drag or Space+drag: pan\nF: fit   1: actual size\n" +
                   "Click: toggle colour   Drag: add colours   Ctrl+drag: remove",
        };
        viewFlow.Controls.AddRange(new Control[] { keyViewBox, framesBox, modeDrift, modeKey, help });
        viewGroup.Controls.Add(viewFlow);

        var selectRow = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true };
        var selectNear = new Button { Text = "Select within", AutoSize = true };
        var selectNone = new Button { Text = "Select none", AutoSize = true };
        selectNear.Click += (_, _) => SelectWithinDistance();
        selectNone.Click += (_, _) => { view.Highlight.Clear(); RefreshColors(); view.Rebuild(); SelectionChanged(); };
        selectRow.Controls.AddRange(new Control[]
        {
            selectNear, distanceBox, new Label { Text = "of key", AutoSize = true, Margin = new Padding(0, 6, 0, 0) }, selectNone,
        });

        colorList.Columns.Add("Colour", 85);
        colorList.Columns.Add("Dist", 45, HorizontalAlignment.Right);
        colorList.Columns.Add("565=key", 60);
        colorList.Columns.Add("Outside", 62, HorizontalAlignment.Right);
        colorList.Columns.Add("Inside", 62, HorizontalAlignment.Right);
        colorList.ItemChecked += (_, e) => OnColorChecked(e.Item);
        colorList.ItemActivate += (_, _) => CenterOnSelectedColor();

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
        side.Controls.Add(selectRow, 0, 2);
        side.Controls.Add(selectedLabel, 0, 3);
        side.Controls.Add(colorList, 0, 4);
        side.Controls.Add(actions, 0, 5);

        var inner = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel2 };
        inner.Panel1.Controls.Add(view);
        inner.Panel2.Controls.Add(side);
        var outer = new SplitContainer { Dock = DockStyle.Fill, FixedPanel = FixedPanel.Panel1 };
        outer.Panel1.Controls.Add(tree);
        outer.Panel2.Controls.Add(inner);

        Controls.Add(outer);
        Controls.Add(statusStrip);
        Controls.Add(menu);
        MainMenuStrip = menu;

        Load += (_, _) =>
        {
            outer.SplitterDistance = 340;
            inner.SplitterDistance = Math.Max(200, inner.Width - 380);
        };

        tree.BeforeExpand += (_, e) => { if (e.Node != null) PopulatePak(e.Node); };
        tree.AfterSelect += (_, e) => OnTreeSelect(e.Node);
        keyViewBox.CheckedChanged += (_, _) => { view.KeyView = keyViewBox.Checked; view.Rebuild(); };
        framesBox.CheckedChanged += (_, _) => { view.ShowFrames = framesBox.Checked; view.Invalidate(); };
        view.PixelHovered += OnHover;
        view.PixelClicked += OnPixelClicked;
        view.PixelDragged += OnPixelDragged;
        view.ZoomChanged += () => zoomLabel.Text = $"{view.Zoom * 100:0}%";
        DragEnter += (_, e) => e.Effect = e.Data?.GetDataPresent(DataFormats.FileDrop) == true ? DragDropEffects.Copy : DragDropEffects.None;
        DragDrop += (_, e) => { if (e.Data?.GetData(DataFormats.FileDrop) is string[] paths) OpenPaths(paths); };
        FormClosing += (_, e) => { if (!ConfirmDiscard(docs.Where(d => d.Dirty))) e.Cancel = true; };
        SelectionChanged();
    }

    // ---------- opening ----------

    private void OpenFilesDialog()
    {
        using var dlg = new OpenFileDialog { Filter = "PAK files (*.pak)|*.pak|All files|*.*", Multiselect = true };
        if (dlg.ShowDialog(this) == DialogResult.OK) OpenPaths(dlg.FileNames);
    }

    private void OpenFolderDialog()
    {
        using var dlg = new FolderBrowserDialog { Description = "Open every .pak in this folder and its subfolders" };
        if (dlg.ShowDialog(this) == DialogResult.OK) OpenPaths(new[] { dlg.SelectedPath });
    }

    // Files open as PAK nodes; folders open as a tree of every .pak below them.
    private void OpenPaths(IEnumerable<string> paths)
    {
        int added = 0;
        tree.BeginUpdate();
        try
        {
            foreach (var raw in paths)
            {
                string path = Path.GetFullPath(raw);
                if (Directory.Exists(path)) added += AddFolder(path);
                else if (File.Exists(path) && AddPak(path, tree.Nodes) != null) added++;
            }
        }
        finally { tree.EndUpdate(); }
        status.Text = $"Opened {added} PAK files ({docs.Count} open).";
    }

    private int AddFolder(string folder)
    {
        string[] files;
        try
        {
            files = Directory.EnumerateFiles(folder, "*.pak", new EnumerationOptions
            {
                RecurseSubdirectories = true, MatchCasing = MatchCasing.CaseInsensitive, IgnoreInaccessible = true,
            }).OrderBy(f => f, StringComparer.OrdinalIgnoreCase).ToArray();
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Cannot read folder", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 0;
        }
        if (files.Length == 0) { status.Text = "No .pak files under " + folder; return 0; }
        var root = new TreeNode(folder) { Tag = folder };
        tree.Nodes.Add(root);
        int added = 0;
        foreach (var f in files)
        {
            // folder nodes for each subdirectory between the root and the file
            var parent = root;
            string rel = Path.GetRelativePath(folder, Path.GetDirectoryName(f)!);
            if (rel != ".")
                foreach (var part in rel.Split(Path.DirectorySeparatorChar))
                {
                    var next = parent.Nodes.Cast<TreeNode>().FirstOrDefault(n => n.Tag is string && n.Text == part);
                    if (next == null) { next = new TreeNode(part) { Tag = Path.Combine((string)parent.Tag!, part) }; parent.Nodes.Add(next); }
                    parent = next;
                }
            if (AddPak(f, parent.Nodes) != null) added++;
        }
        if (root.Nodes.Count == 0) root.Remove(); else root.Expand();
        return added;
    }

    private TreeNode? AddPak(string path, TreeNodeCollection into)
    {
        if (docs.Any(d => string.Equals(d.Path, path, StringComparison.OrdinalIgnoreCase))) return null;
        var doc = new PakDocument(path);
        docs.Add(doc);
        var node = new TreeNode(doc.Name) { Tag = doc };
        node.Nodes.Add(new TreeNode("...") { Tag = null });   // placeholder until expanded
        into.Add(node);
        return node;
    }

    private void PopulatePak(TreeNode node)
    {
        if (node.Tag is not PakDocument doc) return;
        if (node.Nodes.Count == 1 && node.Nodes[0].Tag == null)
        {
            if (!doc.EnsureLoaded())
            {
                // keep the placeholder so expanding again retries
                node.Text = doc.Name + "  (cannot open)";
                node.ToolTipText = doc.LoadError;
                status.Text = $"{doc.Name}: {doc.LoadError}";
                return;
            }
            node.Nodes.Clear();
            node.Text = doc.Name;
            node.ToolTipText = "";
            for (int i = 0; i < doc.SheetCount; i++)
                node.Nodes.Add(new TreeNode(DescribeSheet(doc, i)) { Tag = new SheetRef(doc, i) });
        }
    }

    private static TreeNode? FindPakNode(TreeNodeCollection nodes, PakDocument doc)
    {
        foreach (TreeNode n in nodes)
        {
            if (n.Tag == doc) return n;
            var found = FindPakNode(n.Nodes, doc);
            if (found != null) return found;
        }
        return null;
    }

    private static string DescribeSheet(PakDocument doc, int i)
    {
        var sprite = doc.Sprite(i);
        var s = doc.Sheets[i];
        if (s == null)
        {
            PAKLib.SpriteImage.TryGetSize(sprite.data, out int w, out int h);
            string k = sprite.ColorKey.HasValue ? "K " + (sprite.ColorKey.Value & 0xFFFFFF).ToString("X6") : "  ------";
            return $" {i,3} {w,4}x{h,-4} {sprite.Rectangles.Count,3}f {k}";
        }
        string key = (s.KeyAuthored ? "K " : "  ") + s.Key.ToString("X6");
        string mark = s.Modified || s.KeyChanged ? "*" : " ";
        return $"{mark}{i,3} {s.Width,4}x{s.Height,-4} {sprite.Rectangles.Count,3}f {key} {(s.DriftOutside > 0 ? "drift " + s.DriftOutside : "")}";
    }

    private void RefreshNodes(PakDocument doc, bool allSheets)
    {
        var node = FindPakNode(tree.Nodes, doc);
        if (node == null) return;
        node.Text = doc.Name + (doc.Dirty ? " *" : "");
        if (!doc.IsLoaded) return;
        foreach (TreeNode child in node.Nodes)
            if (child.Tag is SheetRef r && (allSheets || (doc == sheetDoc && r.Index == currentIndex)))
                child.Text = DescribeSheet(doc, r.Index);
    }

    // ---------- selection in the tree ----------

    private void OnTreeSelect(TreeNode? node)
    {
        if (syncing || node == null) return;
        if (node.Tag is PakDocument doc)
        {
            node.Expand();
            currentDoc = doc;
            return;
        }
        if (node.Tag is SheetRef r) ShowSheet(r.Doc, r.Index);
    }

    private void ShowSheet(PakDocument doc, int i)
    {
        currentDoc = doc;
        sheetDoc = doc;
        currentIndex = i;
        try { current = doc.Sheet(i); }
        catch (Exception ex)
        {
            current = null;
            status.Text = $"{doc.Name} sheet {i} cannot be decoded: {ex.Message}";
        }
        view.Highlight.Clear();
        var rects = current == null
            ? Enumerable.Empty<Rectangle>()
            : doc.Sprite(i).Rectangles.Select(r => new Rectangle(r.x, r.y, r.width, r.height));
        view.SetSheet(current, rects);
        RefreshNodes(doc, allSheets: false);
        RefreshKey();
        RefreshColors();
        SelectionChanged();
    }

    // ---------- saving and closing ----------

    private void SaveDoc(PakDocument? doc, string? path)
    {
        if (doc == null || !doc.IsLoaded) return;
        try
        {
            doc.Save(path ?? doc.Path);
            RefreshNodes(doc, allSheets: true);
            RefreshKey();
            status.Text = "Saved " + doc.Path;
        }
        catch (Exception ex)
        {
            MessageBox.Show(this, ex.Message, "Cannot save " + doc.Name, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    private void SaveAs()
    {
        if (currentDoc == null || !currentDoc.IsLoaded) return;
        using var dlg = new SaveFileDialog { Filter = "PAK files (*.pak)|*.pak", FileName = currentDoc.Name };
        if (dlg.ShowDialog(this) == DialogResult.OK) SaveDoc(currentDoc, dlg.FileName);
    }

    private void SaveAll()
    {
        foreach (var d in docs.Where(d => d.Dirty).ToList()) SaveDoc(d, null);
    }

    private bool ConfirmDiscard(IEnumerable<PakDocument> dirty)
    {
        var names = dirty.Select(d => d.Name).ToList();
        if (names.Count == 0) return true;
        return MessageBox.Show(this, "Discard unsaved changes in:\n" + string.Join("\n", names.Take(20)) +
            (names.Count > 20 ? $"\n... and {names.Count - 20} more" : ""), "Colour Key Editor",
            MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
    }

    private void CloseDoc(PakDocument? doc)
    {
        if (doc == null || !ConfirmDiscard(doc.Dirty ? new[] { doc } : Array.Empty<PakDocument>())) return;
        if (sheetDoc == doc) ClearView();
        if (currentDoc == doc) currentDoc = null;
        var node = FindPakNode(tree.Nodes, doc);
        docs.Remove(doc);
        if (node != null)
        {
            var parent = node.Parent;
            node.Remove();
            // drop folder nodes left empty
            while (parent != null && parent.Nodes.Count == 0) { var up = parent.Parent; parent.Remove(); parent = up; }
        }
    }

    private void CloseAll()
    {
        if (!ConfirmDiscard(docs.Where(d => d.Dirty))) return;
        docs.Clear();
        tree.Nodes.Clear();
        ClearView();
    }

    private void ClearView()
    {
        currentDoc = null;
        sheetDoc = null;
        current = null;
        currentIndex = -1;
        view.Highlight.Clear();
        view.SetSheet(null, Array.Empty<Rectangle>());
        RefreshKey();
        RefreshColors();
        SelectionChanged();
    }

    private void Changed()
    {
        if (sheetDoc == null) return;
        view.Rebuild();
        RefreshKey();
        RefreshColors();
        RefreshNodes(sheetDoc, allSheets: false);
        SelectionChanged();
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
        current.UpdateDrift();
        view.Highlight.Clear();
        Changed();
    }

    private void ChooseKey()
    {
        if (current == null) return;
        using var dlg = new ColorDialog { Color = Color.FromArgb(current.Key | unchecked((int)0xFF000000)), FullOpen = true };
        if (dlg.ShowDialog(this) == DialogResult.OK) SetKey(dlg.Color.ToArgb());
    }

    // ---------- colour selection (canvas and list) ----------

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

    // List -> canvas
    private void OnColorChecked(ListViewItem item)
    {
        if (syncing || item.Tag is not int color) return;
        if (item.Checked) view.Highlight.Add(color); else view.Highlight.Remove(color);
        view.Rebuild();
        SelectionChanged();
    }

    // Canvas -> list: sync the check box of a colour (adding a row if it is beyond the list cap).
    private void SyncCheck(int color)
    {
        bool listed = false;
        syncing = true;
        try
        {
            foreach (ListViewItem item in colorList.Items)
                if (item.Tag is int c && c == color)
                {
                    item.Checked = view.Highlight.Contains(c);
                    item.EnsureVisible();
                    item.Selected = true;
                    listed = true;
                    break;
                }
        }
        finally { syncing = false; }
        if (!listed && view.Highlight.Contains(color)) RefreshColors();
        view.Rebuild();
        SelectionChanged();
    }

    private void SelectionChanged()
    {
        int px = 0;
        if (current != null && view.Highlight.Count > 0)
            foreach (int c in current.Pixels) if (view.Highlight.Contains(c)) px++;
        selectedLabel.Text = $"Selected: {view.Highlight.Count} colours, {px} pixels";
    }

    private void SelectWithinDistance()
    {
        if (current == null) return;
        int max = (int)distanceBox.Value;
        foreach (var c in stats)
            if (c.Distance <= max && c.Color != current.Key) view.Highlight.Add(c.Color);
        RefreshColors();
        view.Rebuild();
        SelectionChanged();
    }

    // Double-click a list row: centre the canvas on the first pixel of that colour.
    private void CenterOnSelectedColor()
    {
        if (current == null || colorList.SelectedItems.Count == 0 || colorList.SelectedItems[0].Tag is not int color) return;
        int i = Array.IndexOf(current.Pixels, color);
        if (i < 0) return;
        view.CenterOn(i % current.Width, i / current.Width);
        status.Text = $"{color:X6} first at ({i % current.Width}, {i / current.Width}).";
    }

    // ---------- edits ----------

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
        var doc = currentDoc;
        if (doc == null || !doc.EnsureLoaded() || doc.SheetCount == 0) return;
        if (MessageBox.Show(this,
                $"For every sheet of {doc.Name}: set the key to its first pixel and clear everything outside the frames to it.\n" +
                "This replaces any key already authored on those sheets.\n" +
                "Sheets whose first pixel lies inside a frame are skipped.", "Clear all sheets",
                MessageBoxButtons.OKCancel) != DialogResult.OK) return;
        int done = 0, skipped = 0;
        long pixels = 0;
        Cursor = Cursors.WaitCursor;
        try
        {
            for (int i = 0; i < doc.SheetCount; i++)
            {
                SheetImage s;
                try { s = doc.Sheet(i); } catch { skipped++; continue; }
                if (s.InFrame.Length == 0 || s.InFrame[0]) { skipped++; continue; }
                if (s.Key != s.FirstPixel) { s.Key = s.FirstPixel; s.KeyAuthored = true; s.UpdateDrift(); }
                int n = s.ClearOutsideFrames();
                if (n > 0) s.KeyAuthored = true;
                pixels += n;
                done++;
            }
        }
        finally { Cursor = Cursors.Default; }
        view.Highlight.Clear();
        if (doc == sheetDoc && current != null) { view.Rebuild(); RefreshKey(); RefreshColors(); }
        RefreshNodes(doc, allSheets: true);
        SelectionChanged();
        status.Text = $"{doc.Name}: cleared {pixels} pixels on {done} sheets; skipped {skipped}.";
    }

    // ---------- canvas input ----------

    private void OnHover(int x, int y)
    {
        if (current == null || x < 0) { hover.Text = ""; return; }
        int i = y * current.Width + x;
        int c = current.Pixels[i];
        string what = c == current.Key ? "key" : $"dist {SheetImage.Distance(c, current.Key)}";
        hover.Text = $"({x}, {y})  {c:X6}  {what}  {(current.InFrame[i] ? "in frame" : "outside")}";
    }

    private void OnPixelClicked(int x, int y, Keys mods)
    {
        if (current == null) return;
        int c = current.Pixels[y * current.Width + x];
        if (modeKey.Checked) { SetKey(c); return; }
        if (c == current.Key) return;
        if (!view.Highlight.Add(c)) view.Highlight.Remove(c);
        SyncCheck(c);
    }

    private void OnPixelDragged(int x, int y, Keys mods)
    {
        if (current == null || modeKey.Checked) return;
        int c = current.Pixels[y * current.Width + x];
        if (c == current.Key) return;
        bool changed = (mods & Keys.Control) != 0 ? view.Highlight.Remove(c) : view.Highlight.Add(c);
        if (changed) SyncCheck(c);
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) treeFont.Dispose();
        base.Dispose(disposing);
    }
}
