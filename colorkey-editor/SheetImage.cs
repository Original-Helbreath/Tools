using PAKLib;

namespace ColorKeyEditor;

// One sprite sheet as editable pixels (0xRRGGBB, top-down rows), with its frame mask and colour-key state.
public sealed class SheetImage
{
    public int Width { get; }
    public int Height { get; }
    public int[] Pixels { get; }
    public bool[] InFrame { get; }          // true where any frame rectangle covers the pixel
    public int Key { get; set; }            // the key colour this sheet is edited towards (0xRRGGBB)
    public bool KeyAuthored { get; set; }   // true when the pak (or the user) set the key, false when derived
    public bool Modified { get; private set; }
    public int DriftOutside { get; private set; }   // pixels outside every frame that are not the key

    private int[]? original;                        // pixels as last opened or saved; kept from the first edit
    private int savedKey;
    private bool savedAuthored;

    private SheetImage(int width, int height, int[] pixels, bool[] inFrame, int key, bool authored)
    {
        Width = width;
        Height = height;
        Pixels = pixels;
        InFrame = inFrame;
        Key = key;
        KeyAuthored = authored;
        savedKey = key;
        savedAuthored = authored;
        UpdateDrift();
    }

    // Decodes a sprite (BMP of any depth, or PNG) through PAKLib's 24bpp conversion, so the pixels are exactly what
    // a 24bpp save would write.
    public static SheetImage FromSprite(Sprite sprite)
    {
        byte[] bmp = SpriteImage.ToBmp24(sprite.data, sprite.ColorKey);
        int width = BitConverter.ToInt32(bmp, 18);
        int height = BitConverter.ToInt32(bmp, 22);
        int offBits = BitConverter.ToInt32(bmp, 10);
        bool bottomUp = height > 0;
        height = Math.Abs(height);
        int rowSize = (width * 3 + 3) & ~3;
        var pixels = new int[width * height];
        for (int y = 0; y < height; y++)
        {
            int row = offBits + (bottomUp ? height - 1 - y : y) * rowSize;
            for (int x = 0; x < width; x++)
            {
                int p = row + x * 3;
                pixels[y * width + x] = bmp[p + 2] << 16 | bmp[p + 1] << 8 | bmp[p];
            }
        }

        var inFrame = new bool[width * height];
        foreach (var r in sprite.Rectangles)
        {
            if (r.width == 0 || r.height == 0) continue;
            int x1 = Math.Min(width, r.x + r.width), y1 = Math.Min(height, r.y + r.height);
            for (int y = r.y; y < y1; y++)
                for (int x = r.x; x < x1; x++)
                    inFrame[y * width + x] = true;
        }

        bool authored = sprite.ColorKey.HasValue;
        int key = authored ? (int)(sprite.ColorKey!.Value & 0xFFFFFF) : (pixels.Length > 0 ? pixels[0] : 0);
        return new SheetImage(width, height, pixels, inFrame, key, authored);
    }

    // 24bpp bottom-up BMP with a plain 40-byte header, the layout the client reads.
    public byte[] ToBmp24()
    {
        int rowSize = (Width * 3 + 3) & ~3;
        var bmp = new byte[54 + rowSize * Height];
        bmp[0] = 0x42; bmp[1] = 0x4D;
        BitConverter.TryWriteBytes(bmp.AsSpan(2), bmp.Length);
        BitConverter.TryWriteBytes(bmp.AsSpan(10), 54);
        BitConverter.TryWriteBytes(bmp.AsSpan(14), 40);
        BitConverter.TryWriteBytes(bmp.AsSpan(18), Width);
        BitConverter.TryWriteBytes(bmp.AsSpan(22), Height);
        BitConverter.TryWriteBytes(bmp.AsSpan(26), (short)1);
        BitConverter.TryWriteBytes(bmp.AsSpan(28), (short)24);
        BitConverter.TryWriteBytes(bmp.AsSpan(34), rowSize * Height);
        for (int y = 0; y < Height; y++)
        {
            int row = 54 + (Height - 1 - y) * rowSize;
            for (int x = 0; x < Width; x++)
            {
                int c = Pixels[y * Width + x];
                bmp[row + x * 3] = (byte)c;
                bmp[row + x * 3 + 1] = (byte)(c >> 8);
                bmp[row + x * 3 + 2] = (byte)(c >> 16);
            }
        }
        return bmp;
    }

    public int FirstPixel => Pixels.Length > 0 ? Pixels[0] : 0;

    // Every colour on the sheet except the key, with how often it appears outside and inside frames.
    // True when the key differs from what was opened or last saved.
    public bool KeyChanged => Key != savedKey || KeyAuthored != savedAuthored;

    public void UpdateDrift()
    {
        int n = 0;
        for (int i = 0; i < Pixels.Length; i++)
            if (!InFrame[i] && Pixels[i] != Key) n++;
        DriftOutside = n;
    }

    private void BeforeEdit() => original ??= (int[])Pixels.Clone();

    public List<ColorStat> CountColors()
    {
        var map = new Dictionary<int, ColorStat>();
        for (int i = 0; i < Pixels.Length; i++)
        {
            int c = Pixels[i];
            if (c == Key) continue;
            if (!map.TryGetValue(c, out var s)) map[c] = s = new ColorStat(c, Key);
            if (InFrame[i]) s.Inside++; else s.Outside++;
        }
        return map.Values.ToList();
    }

    // Replaces every pixel of the given colours with the key. Returns the number of pixels changed.
    public int ReplaceWithKey(IReadOnlySet<int> colors)
    {
        int n = 0;
        BeforeEdit();
        for (int i = 0; i < Pixels.Length; i++)
            if (Pixels[i] != Key && colors.Contains(Pixels[i])) { Pixels[i] = Key; n++; }
        if (n > 0) Modified = true;
        UpdateDrift();
        return n;
    }

    // Sets every pixel no frame covers to the key. Returns the number of pixels changed.
    public int ClearOutsideFrames()
    {
        int n = 0;
        BeforeEdit();
        for (int i = 0; i < Pixels.Length; i++)
            if (!InFrame[i] && Pixels[i] != Key) { Pixels[i] = Key; n++; }
        if (n > 0) Modified = true;
        UpdateDrift();
        return n;
    }

    // Back to the pixels and key as opened or last saved.
    public void Revert()
    {
        if (original != null) Array.Copy(original, Pixels, Pixels.Length);
        Key = savedKey;
        KeyAuthored = savedAuthored;
        Modified = false;
        UpdateDrift();
    }

    // Call after the sheet was written: the saved state becomes the new revert point.
    public void MarkSaved()
    {
        original = null;
        savedKey = Key;
        savedAuthored = KeyAuthored;
        Modified = false;
    }

    // The client draws in 16-bit RGB565 and compares against the top-left pixel converted the same way, so two
    // colours with the same 565 value are the same colour in game.
    public static int To565(int c) => ((c >> 19) & 0x1F) << 11 | ((c >> 10) & 0x3F) << 5 | ((c >> 3) & 0x1F);

    public static int Distance(int a, int b) =>
        Math.Max(Math.Abs((a >> 16 & 0xFF) - (b >> 16 & 0xFF)),
        Math.Max(Math.Abs((a >> 8 & 0xFF) - (b >> 8 & 0xFF)), Math.Abs((a & 0xFF) - (b & 0xFF))));
}

public sealed class ColorStat
{
    public int Color { get; }
    public int Distance { get; }       // largest per-channel difference from the key
    public bool SameIn565 { get; }     // already transparent in the client (same 16-bit value as the key)
    public int Outside { get; set; }
    public int Inside { get; set; }

    public ColorStat(int color, int key)
    {
        Color = color;
        Distance = SheetImage.Distance(color, key);
        SameIn565 = SheetImage.To565(color) == SheetImage.To565(key);
    }
}
