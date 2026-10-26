using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace ColorKeyEditor;

// Draws a sheet at an integer zoom with optional frame outlines and a transparency-key view:
// key pixels become a checkerboard and selected drift colours are shown in a bright highlight colour.
public sealed class SheetView : Control
{
    private SheetImage? sheet;
    private Bitmap? bitmap;
    private Rectangle[] frames = Array.Empty<Rectangle>();

    public int Zoom { get; private set; } = 1;
    public bool ShowFrames { get; set; } = true;
    public bool KeyView { get; set; }
    public HashSet<int> Highlight { get; } = new();
    public Color HighlightColor { get; set; } = Color.Magenta;

    public event Action<int, int>? PixelHovered;      // x, y in sheet pixels, or -1, -1
    public event Action<int, int, MouseButtons>? PixelClicked;

    public SheetView()
    {
        DoubleBuffered = true;
        SetStyle(ControlStyles.ResizeRedraw, true);
    }

    public void SetSheet(SheetImage? s, IEnumerable<Rectangle> frameRects)
    {
        sheet = s;
        frames = frameRects.ToArray();
        Rebuild();
    }

    public void SetZoom(int zoom)
    {
        Zoom = Math.Clamp(zoom, 1, 16);
        UpdateSize();
        Invalidate();
    }

    // Re-render the cached bitmap after pixels, key, highlight or view mode change.
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
                    row[x] = c;
                }
                System.Runtime.InteropServices.Marshal.Copy(row, 0, data.Scan0 + y * data.Stride, sheet.Width);
            }
            bitmap.UnlockBits(data);
        }
        UpdateSize();
        Invalidate();
    }

    private void UpdateSize()
    {
        Size = sheet == null ? new Size(1, 1) : new Size(sheet.Width * Zoom, sheet.Height * Zoom);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        e.Graphics.Clear(Color.FromArgb(40, 40, 40));
        if (bitmap == null) return;
        e.Graphics.InterpolationMode = InterpolationMode.NearestNeighbor;
        e.Graphics.PixelOffsetMode = PixelOffsetMode.Half;
        e.Graphics.DrawImage(bitmap, new Rectangle(0, 0, bitmap.Width * Zoom, bitmap.Height * Zoom));
        if (ShowFrames)
        {
            e.Graphics.PixelOffsetMode = PixelOffsetMode.Default;
            using var pen = new Pen(Color.FromArgb(200, 0, 255, 255));
            foreach (var f in frames)
                if (f.Width > 0 && f.Height > 0)
                    e.Graphics.DrawRectangle(pen, f.X * Zoom, f.Y * Zoom, f.Width * Zoom - 1, f.Height * Zoom - 1);
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) bitmap?.Dispose();
        base.Dispose(disposing);
    }

    private (int, int) ToSheet(Point p)
    {
        if (sheet == null) return (-1, -1);
        int x = p.X / Zoom, y = p.Y / Zoom;
        return x >= 0 && y >= 0 && x < sheet.Width && y < sheet.Height ? (x, y) : (-1, -1);
    }

    protected override void OnMouseMove(MouseEventArgs e)
    {
        base.OnMouseMove(e);
        var (x, y) = ToSheet(e.Location);
        PixelHovered?.Invoke(x, y);
    }

    protected override void OnMouseLeave(EventArgs e)
    {
        base.OnMouseLeave(e);
        PixelHovered?.Invoke(-1, -1);
    }

    protected override void OnMouseClick(MouseEventArgs e)
    {
        base.OnMouseClick(e);
        var (x, y) = ToSheet(e.Location);
        if (x >= 0) PixelClicked?.Invoke(x, y, e.Button);
    }
}
