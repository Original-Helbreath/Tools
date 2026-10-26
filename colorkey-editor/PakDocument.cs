using PAKLib;

namespace ColorKeyEditor;

// One open .pak file: loaded on first use, with its sheets decoded on first view.
public sealed class PakDocument
{
    public string Path { get; private set; }
    public PAK? Pak { get; private set; }
    public List<SheetImage?> Sheets { get; } = new();
    public bool Dirty => Sheets.Any(s => s != null && (s.Modified || s.KeyChanged));
    public string? LoadError { get; private set; }
    public bool Loading { get; set; }   // a background load is running (UI thread only)
    // Drift of sheets that were decoded and then released, so the tree can still show it.
    public System.Collections.Concurrent.ConcurrentDictionary<int, int> KnownDrift { get; } = new();

    public PakDocument(string path) => Path = path;

    public bool IsLoaded => Pak != null;
    public int SheetCount => Pak?.Data?.Sprites.Count ?? 0;
    public string Name => System.IO.Path.GetFileName(Path);

    public bool EnsureLoaded()
    {
        if (Pak != null) return true;
        try
        {
            var loaded = PAK.ReadFromFile(Path);
            if (loaded.Data == null) throw new InvalidDataException("No PAK data.");
            Pak = loaded;
            Sheets.Clear();
            Sheets.AddRange(Enumerable.Repeat<SheetImage?>(null, loaded.Data.Sprites.Count));
            LoadError = null;
            return true;
        }
        catch (Exception ex)
        {
            LoadError = ex.Message;
            return false;
        }
    }

    public Sprite Sprite(int i) => Pak!.Data!.Sprites[i];

    // Decodes the sheet on first use; throws if the image cannot be decoded.
    public SheetImage Sheet(int i) => Sheets[i] ??= SheetImage.FromSprite(Sprite(i));

    // Drops a decoded sheet that has nothing unsaved, so only the sheet on screen and edited sheets stay in memory.
    public void Release(int i)
    {
        var s = Sheets[i];
        if (s == null || s.Modified || s.KeyChanged) return;
        KnownDrift[i] = s.DriftOutside;
        Sheets[i] = null;
    }

    // Writes changed sheets and keys into the PAK and saves it to `path` (the same file, or Save As).
    public void Save(string path)
    {
        if (Pak?.Data == null) return;
        for (int i = 0; i < Sheets.Count; i++)
        {
            var s = Sheets[i];
            if (s == null) continue;   // never opened: written as loaded
            var sprite = Sprite(i);
            if (s.Modified) sprite.data = s.ToBmp24();
            if (s.KeyChanged)
            {
                if (s.KeyAuthored) sprite.SetColorKey((byte)(s.Key >> 16), (byte)(s.Key >> 8), (byte)s.Key);
                else sprite.ClearColorKey();
            }
        }
        PAK.SaveToFile(Pak, path);
        foreach (var s in Sheets) s?.MarkSaved();
        Path = path;
    }
}
