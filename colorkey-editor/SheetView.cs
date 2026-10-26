using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace ColorKeyEditor;

// The sheet canvas: free zoom (mouse wheel, around the cursor), panning (middle or right drag, or Space + left
// drag), frame outlines, a pixel grid at high zoom, and a transparency-key view in which key pixels are a
// checkerboard and selected colours are highlighted.
// Left click picks a pixel; left drag sweeps over pixels (for selecting several colours at once).
public sealed class SheetView : Control
{
    private SheetImage? sheet;
    private Bitmap? bitmap;
    private Rectangle[] frames = Array.Empty<Rectangle>();
    private float zoom = 1f;
    private PointF origin;                 // screen position of the sheet's top-left corner
    private Point panStart;
    private PointF panOrigin;
    private bool panning, spaceDown, dragging, moved;
    private Point downAt;
    private (int X, int Y) lastDragPixel = (-1, -1);

    public bool ShowFrames { get; set; } = true;
    public bool KeyView { get; set; }
    public HashSet<int> Highlight { get; } = new();
    public Color HighlightColor { get; set; } = Color.Magenta;
    public float Zoom => zoom;

    public event Action<int, int>? PixelHovered;               // sheet x, y, or -1, -1
    public event Action<int, int, Keys>? PixelClicked;         // left click without dragging
    public event Action<int, int, Keys>? PixelDragged;         // each new pixel under a left drag
    public event Action? ZoomChanged;

    public SheetView()
    {
        DoubleBuffered = true;
        SetStyle(ControlStyles.ResizeRedraw | ControlStyles.Selectable, true);
        TabStop = true;
        BackColor = Color.FromArgb(40, 40, 40);
    }

    public void SetSheet(SheetImage? s, IEnumerable<Rectangle> frameRects)
    {
        sheet = s;
        frames = frameRects.ToArray();
        Rebuild();
        FitToView();
    }

    // ---------- view transform ----------

    public void FitToView()
    {
        if (sheet == null || Width < 2 || Height < 2) { Invalidate(); return; }
        float z = Math.Min((Width - 20f) / sheet.Width, (Height - 20f) / sheet.Height);
        SetZoomAround(Math.Clamp(z, 0.05f, 64f), new PointF(Width / 2f, Height / 2f), center: true);
    }

    public void ActualSize() => SetZoomAround(1f, new PointF(Width / 2f, Height / 2f), center: false);

    public void ZoomBy(float factor) => SetZoomAround(zoom * factor, new PointF(Width / 2f, Height / 2f), center: false);

    // Zooms keeping the sheet point under `anchor` fixed (or centres the sheet when `center`).
    private void SetZoomAround(float newZoom, PointF anchor, bool center)
    {
        newZoom = Math.Clamp(newZoom, 0.05f, 64f);
        if (sheet != null)
        {
            if (center)
                origin = new PointF((Width - sheet.Width * newZoom) / 2f, (Height - sheet.Height * newZoom) / 2f);
            else
            {
                float sx = (anchor.X - origin.X) / zoom, sy = (anchor.Y - origin.Y) / zoom;
                origin = new PointF(anchor.X - sx * newZoom, anchor.Y - sy * newZoom);
            }
        }
        zoom = newZoom;
        ZoomChanged?.Invoke();
        Invalidate();
    }

    // Scrolls so the given sheet pixel is in the middle of the view.
    public void CenterOn(int x, int y)
    {
        origin = new PointF(Width / 2f - (x + 0.5f) * zoom, Height / 2f - (y + 0.5f) * zoom);
        Invalidate();
    }

    private (int, int) ToSheet(Point p)
    {
        if (sheet == null) return (-1, -1);
        int x = (int)Math.Floor((p.X - origin.X) / zoom), y = (int)Math.Floor((p.Y - origin.Y) / zoom);
        return x >= 0 && y >= 0 && x < sheet.Width && y < sheet.Height ? (x, y) : (-1, -1);
    }

    // ---------- rendering ----------

    // Re-renders the cached bitmap after pixels, key, highlight or view mode change.
    public void Rebuild()
    {
        bitmap?.Dispose();
        bitmap = null;
        if (sheet != null)
        {
            bitmap = new Bitmap(sheet.Width, sheet.Height, PixelFormat.Format32bppRgb);
            var data = bitmap.LockBits(new Rectangle(0, 0, sheet.Width, sheet.Height), ImageLockMode.WriteOnly,
                PixelFormat.Format32bppRgb);
            var row = new int[sheet.Width];
            int hi = HighlightColor.ToArgb() & 0xFFFFFF;
            for (int y = 0; y < sheet.Height; y++)
            {
                for (int x = 0; x < sheet.Width; x++)
                {
                    int c = sheet.Pixels[y * sheet.Width + x];
                    if (KeyView)
                    {
                        if (c == sheet.Key) c = ((x >> 2) + (y >> 2)) % 2 == 0 ? 0xC8C8C8 : 0x8C8C8C;
                        else if (Highlight.Contains(c)) c = hi;
                    }
                    else if (Highlight.Contains(c)) c = hi;
                    row[x] = c;
                }
                System.Runtime.InteropServices.Marshal.Copy(row, 0, data.Scan0 + y * data.Stride, sheet.Width);
            }
            bitmap.UnlockBits(data);
        }
        Invalidate();
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.Clear(BackColor);
        if (bitmap == null || sheet == null) return;
        g.InterpolationMode = InterpolationMode.NearestNeighbor;
        g.PixelOffsetMode = PixelOffsetMode.Half;
        var dest = new RectangleF(origin.X, origin.Y, sheet.Width * zoom, sheet.Height * zoom);
        g.DrawImage(bitmap, dest);
        g.PixelOffsetMode = PixelOffsetMode.Default;
        using (var border = new Pen(Color.FromArgb(90, 90, 90)))
            g.DrawRectangle(border, dest.X - 1, dest.Y - 1, dest.Width + 1, dest.Height + 1);

        if (zoom >= 12)
        {
            // pixel grid over the visible part only
            using var grid = new Pen(Color.FromArgb(60, 0, 0, 0));
            int x0 = Math.Max(0, (int)(-origin.X / zoom)), x1 = Math.Min(sheet.Width, (int)((Width - origin.X) / zoom) + 1);
            int y0 = Math.Max(0, (int)(-origin.Y / zoom)), y1 = Math.Min(sheet.Height, (int)((Height - origin.Y) / zoom) + 1);
            for (int x = x0; x <= x1; x++) g.DrawLine(grid, origin.X + x * zoom, origin.Y + y0 * zoom, origin.X + x * zoom, origin.Y + y1 * zoom);
            for (int y = y0; y <= y1; y++) g.DrawLine(grid, origin.X + x0 * zoom, origin.Y + y * zoom, origin.X + x1 * zoom, origin.Y + y * zoom);
        }

        if (ShowFrames)
        {
            using var pen = new Pen(Color.FromArgb(200, 0, 255, 255));
            foreach (var f in frames)
                if (f.Width > 0 && f.Height > 0)
                    g.DrawRectangle(pen, origin.X + f.X * zoom, origin.Y + f.Y * zoom, f.Width * zoom - 1, f.Height * zoom - 1);
        }
    }

    // ---------- input ----------

    protected override bool IsInputKey(Keys keyData) => keyData == Keys.Space || base.IsInputKey(keyData);

    protected override void OnKeyDown(KeyEventArgs e)
    {
        base.OnKeyDown(e);
        if (e.KeyCode == Keys.Space) { spaceDown = true; Cursor = Cursors.Hand; }
        else if (e.KeyCode == Keys.F) FitToView();
        else if (e.KeyCode == Keys.D1 || e.KeyCode == Keys.NumPad1) ActualSize();
        else if (e.KeyCode == Keys.Oemplus || e.KeyCode == Keys.Add) ZoomBy(1.25f);
        else if (e.KeyCode == Keys.OemMinus || e.KeyCode == Keys.Subtract) ZoomBy(0.8f);
    }

    protected override void OnKeyUp(KeyEventArgs e)
    {
        base.OnKeyUp(e);
        if (e.KeyCode == Keys.Space) { spaceDown = false; if (!panning) Cursor = Cursors.Default; }
    }

    protected override void OnMouseWheel(MouseEventArgs e)
    {
        base.OnMouseWheel(e);
        SetZoomAround(zoom * (e.Delta > 0 ? 1.25f : 0.8f), e.Location, center: false);
    }

    protected override void OnMouseDown(MouseEventArgs e)
    {
        base.OnMouseDown(e);
        Focus();
        if (e.Button == MouseButtons.Middle || e.Button == MouseButtons.Right || (e.Button == MouseButtons.Left && spaceDown))
        {
            panning = true;
            panStart = e.Location;
            panOrigin = origin;
            Cursor = Cursors.SizeAll;
            return;
        }
        if (e.Button == MouseButtons.Left)
        {
            dragging = true;
            moved = false;
            downAt = e.Location;
            lastDragPixel = ToSheet(e.Location);
        }
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        var (x, y) = ToSheet(e.Location);
        PixelHovered?.Invoke(x, y);
        if (panning)
        {
            origin = new PointF(panOrigin.X + e.X - panStart.X, panOrigin.Y + e.Y - panStart.Y);
            Invalidate();
            return;
        }
        if (dragging && (e.Button & MouseButtons.Left) != 0)
        {
            if (!moved && Math.Abs(e.X - downAt.X) + Math.Abs(e.Y - downAt.Y) > 3)
            {
                moved = true;
                if (lastDragPixel.X >= 0) PixelDragged?.Invoke(lastDragPixel.X, lastDragPixel.Y, ModifierKeys);
            }
            if (moved && x >= 0 && (x, y) != lastDragPixel)
            {
                lastDragPixel = (x, y);
                PixelDragged?.Invoke(x, y, ModifierKeys);
            }
        }
    }

    protected override void OnMouseUp(MouseEventArgs e)
    {
        base.OnMouseUp(e);
        if (panning)
        {
            panning = false;
            Cursor = spaceDown ? Cursors.Hand : Cursors.Default;
            return;
        }
        if (dragging && e.Button == MouseButtons.Left)
        {
            dragging = false;
            if (!moved)
            {
                var (x, y) = ToSheet(e.Location);
                if (x >= 0) PixelClicked?.Invoke(x, y, ModifierKeys);
            }
        }
    }

    // Drop held-key and drag state when focus or capture is lost (alt-tab, dialogs).
    protected override void OnLostFocus(EventArgs e)
    {
        base.OnLostFocus(e);
        spaceDown = panning = dragging = false;
        Cursor = Cursors.Default;
    }

    protected override void OnMouseCaptureChanged(EventArgs e)
    {
        base.OnMouseCaptureChanged(e);
        if (!Capture) { panning = dragging = false; Cursor = spaceDown ? Cursors.Hand : Cursors.Default; }
    }

    protected override void OnMouseLeave(EventArgs e)
    {
        base.OnMouseLeave(e);
        PixelHovered?.Invoke(-1, -1);
    }

    protected override void OnResize(EventArgs e)
    {
        base.OnResize(e);
        Invalidate();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) bitmap?.Dispose();
        base.Dispose(disposing);
    }
}
