using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Rhino;
using Rhino.Commands;
using Rhino.DocObjects;
using Rhino.Display;
using Rhino.Geometry;
using Rhino.Input;

namespace RhinoChannelsPlugin.Commands
{
    [SupportedOSPlatform("windows")]
    public sealed class CaptureBaseColorMaskCommand : Command
    {
        private readonly struct LayerMaskEntry
        {
            public LayerMaskEntry(int index, Color color, string name)
            {
                Index = index;
                Color = color;
                Name = name;
            }

            public int Index { get; }
            public Color Color { get; }
            public string Name { get; }
        }

        public override string EnglishName => "CaptureBaseColorMask";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            RhinoApp.WriteLine("CaptureBaseColorMask: command started.");

            var maskPath = string.Empty;
            var viewName = string.Empty;
            var widthToken = string.Empty;
            var heightToken = string.Empty;

            var result = RhinoGet.GetString("MaskPath", false, ref maskPath);
            if (result != Result.Success)
                return result;

            result = RhinoGet.GetString("ViewName (Enter for active view)", true, ref viewName);
            if (result == Result.Cancel)
                return result;

            result = RhinoGet.GetString("Width (0 = viewport width)", true, ref widthToken);
            if (result == Result.Cancel)
                return result;

            result = RhinoGet.GetString("Height (0 = viewport height)", true, ref heightToken);
            if (result == Result.Cancel)
                return result;

            try
            {
                var width = ParseOptionalPositiveInt(widthToken, "Width");
                var height = ParseOptionalPositiveInt(heightToken, "Height");

                var view = string.IsNullOrWhiteSpace(viewName)
                    ? doc.Views.ActiveView
                    : doc.Views.Find(viewName, false);
                if (view == null)
                    throw new InvalidOperationException("No matching view found.");

                view.Redraw();

                var srcSize = view.ActiveViewport.Size;
                if (srcSize.Width <= 0 || srcSize.Height <= 0)
                    throw new InvalidOperationException("Viewport size is invalid.");

                var outWidth = width > 0 ? width : srcSize.Width;
                var outHeight = height > 0 ? height : srcSize.Height;
                if (outWidth <= 0 || outHeight <= 0)
                    throw new InvalidOperationException("Invalid output dimensions.");

                RhinoApp.WriteLine(
                    $"CaptureBaseColorMask: using view '{view.ActiveViewport.Name}', source={srcSize.Width}x{srcSize.Height}, output={outWidth}x{outHeight}.");

                var layerVisibilitySnapshot = SnapshotLayerVisibility(doc);
                var changedAa = TrySetOpenGlAntialiasLevel(0, out var prevAaLevel);

                try
                {
                    view.Redraw();

                    var maskLayers = CollectVisibleLayersWithObjects(doc);
                    RhinoApp.WriteLine($"CaptureBaseColorMask: visible mask layers with objects={maskLayers.Count}.");

                    var srcWidth = srcSize.Width;
                    var srcHeight = srcSize.Height;
                    var pixelCount = srcWidth * srcHeight;

                    var basePoints = new Point3d[pixelCount];
                    var baseValid = new bool[pixelCount];
                    CaptureWorldPoints(view.ActiveViewport, srcWidth, srcHeight, basePoints, baseValid);

                    var srcArgb = new int[pixelCount];
                    var backgroundArgb = Color.White.ToArgb();
                    for (var i = 0; i < srcArgb.Length; i++)
                        srcArgb[i] = backgroundArgb;

                    var pointTolerance = Math.Max(doc.ModelAbsoluteTolerance * 0.1, 1e-6);
                    var pointToleranceSq = pointTolerance * pointTolerance;

                    foreach (var layer in maskLayers)
                    {
                        SetOnlyLayerVisible(doc, layerVisibilitySnapshot.Keys, layer.Index);
                        view.Redraw();

                        var layerPoints = new Point3d[pixelCount];
                        var layerValid = new bool[pixelCount];
                        CaptureWorldPoints(view.ActiveViewport, srcWidth, srcHeight, layerPoints, layerValid);

                        var layerArgb = layer.Color.ToArgb();
                        var hitCount = 0;

                        for (var idx = 0; idx < pixelCount; idx++)
                        {
                            if (!baseValid[idx] || !layerValid[idx])
                                continue;

                            if (!PointsMatch(basePoints[idx], layerPoints[idx], pointToleranceSq))
                                continue;

                            srcArgb[idx] = layerArgb;
                            hitCount++;
                        }

                        RhinoApp.WriteLine($"CaptureBaseColorMask: layer '{layer.Name}' visible pixels={hitCount}.");
                    }

                    WriteMaskPng(maskPath, srcArgb, srcWidth, srcHeight, outWidth, outHeight);
                }
                finally
                {
                    RestoreLayerVisibility(doc, layerVisibilitySnapshot);
                    if (changedAa)
                        TrySetOpenGlAntialiasLevel(prevAaLevel, out _);
                    view.Redraw();
                }

                RhinoApp.WriteLine($"CaptureBaseColorMask: wrote '{maskPath}'.");
                return Result.Success;
            }
            catch (Exception ex)
            {
                RhinoApp.WriteLine($"CaptureBaseColorMask: ERROR {ex.GetType().Name}: {ex.Message}");
                return Result.Failure;
            }
        }

        private static int ParseOptionalPositiveInt(string token, string label)
        {
            if (string.IsNullOrWhiteSpace(token))
                return 0;
            if (!int.TryParse(token, NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
                throw new InvalidOperationException($"{label} '{token}' is not an integer.");
            if (value < 0)
                throw new InvalidOperationException($"{label} must be >= 0.");
            return value;
        }

        private static Dictionary<int, bool> SnapshotLayerVisibility(RhinoDoc doc)
        {
            var snapshot = new Dictionary<int, bool>();
            for (var i = 0; i < doc.Layers.Count; i++)
            {
                var layer = doc.Layers[i];
                if (layer == null || layer.IsDeleted)
                    continue;
                snapshot[i] = layer.IsVisible;
            }
            return snapshot;
        }

        private static void RestoreLayerVisibility(RhinoDoc doc, IReadOnlyDictionary<int, bool> snapshot)
        {
            foreach (var pair in snapshot)
            {
                if (pair.Key < 0 || pair.Key >= doc.Layers.Count)
                    continue;
                SetLayerVisible(doc, pair.Key, pair.Value);
            }
        }

        private static List<LayerMaskEntry> CollectVisibleLayersWithObjects(RhinoDoc doc)
        {
            var usedLayerIndices = new HashSet<int>();
            foreach (var obj in doc.Objects)
            {
                if (obj == null || obj.IsDeleted)
                    continue;
                if (!obj.Attributes.Visible)
                    continue;

                var layerIndex = obj.Attributes.LayerIndex;
                if (layerIndex < 0 || layerIndex >= doc.Layers.Count)
                    continue;

                var layer = doc.Layers[layerIndex];
                if (layer == null || layer.IsDeleted || !layer.IsVisible)
                    continue;

                usedLayerIndices.Add(layerIndex);
            }

            var layers = new List<LayerMaskEntry>();
            for (var i = 0; i < doc.Layers.Count; i++)
            {
                if (!usedLayerIndices.Contains(i))
                    continue;

                var layer = doc.Layers[i];
                if (layer == null || layer.IsDeleted || !layer.IsVisible)
                    continue;

                var name = string.IsNullOrWhiteSpace(layer.FullPath)
                    ? (string.IsNullOrWhiteSpace(layer.Name) ? $"Layer_{i}" : layer.Name)
                    : layer.FullPath;
                layers.Add(new LayerMaskEntry(i, layer.Color, name));
            }

            return layers;
        }

        private static void SetOnlyLayerVisible(
            RhinoDoc doc,
            IEnumerable<int> candidateLayerIndices,
            int targetLayerIndex)
        {
            var visibleLayerIndices = new HashSet<int>();
            if (targetLayerIndex >= 0 && targetLayerIndex < doc.Layers.Count)
            {
                foreach (var layerIndex in EnumerateLayerAncestors(doc, targetLayerIndex))
                    visibleLayerIndices.Add(layerIndex);
            }

            foreach (var layerIndex in candidateLayerIndices)
            {
                SetLayerVisible(doc, layerIndex, visibleLayerIndices.Contains(layerIndex));
            }
        }

        private static IEnumerable<int> EnumerateLayerAncestors(RhinoDoc doc, int layerIndex)
        {
            var currentIndex = layerIndex;
            while (currentIndex >= 0 && currentIndex < doc.Layers.Count)
            {
                yield return currentIndex;

                var current = doc.Layers[currentIndex];
                if (current == null || current.IsDeleted)
                    yield break;

                if (current.ParentLayerId == Guid.Empty)
                {
                    currentIndex = -1;
                    continue;
                }

                var parentLayer = doc.Layers.FindId(current.ParentLayerId);
                currentIndex = parentLayer?.Index ?? -1;
            }
        }

        private static void SetLayerVisible(RhinoDoc doc, int layerIndex, bool visible)
        {
            if (layerIndex < 0 || layerIndex >= doc.Layers.Count)
                return;

            var layer = doc.Layers[layerIndex];
            if (layer == null || layer.IsDeleted)
                return;
            if (layer.IsVisible == visible)
                return;

            try
            {
                layer.IsVisible = visible;
            }
            catch
            {
                // Ignore visibility mutations that Rhino rejects; capture will remain best-effort.
            }
        }

        private static void CaptureWorldPoints(
            RhinoViewport viewport,
            int width,
            int height,
            Point3d[] points,
            bool[] valid)
        {
            using var zcap = new ZBufferCapture(viewport);
            zcap.ShowIsocurves(false);
            zcap.ShowMeshWires(false);
            zcap.ShowCurves(false);
            zcap.ShowPoints(false);
            zcap.ShowText(false);
            zcap.ShowAnnotations(false);
            zcap.ShowLights(false);

            for (var y = 0; y < height; y++)
            {
                for (var x = 0; x < width; x++)
                {
                    var idx = (y * width) + x;
                    var p = zcap.WorldPointAt(x, y);
                    points[idx] = p;
                    valid[idx] = p.IsValid;
                }
            }
        }

        private static bool PointsMatch(Point3d a, Point3d b, double toleranceSquared)
        {
            return a.IsValid && b.IsValid && a.DistanceToSquared(b) <= toleranceSquared;
        }

        private static void WriteMaskPng(
            string path,
            int[] srcArgb,
            int srcWidth,
            int srcHeight,
            int outWidth,
            int outHeight)
        {
            using var bitmap = new Bitmap(outWidth, outHeight, PixelFormat.Format32bppArgb);
            var rect = new Rectangle(0, 0, outWidth, outHeight);
            var dstData = bitmap.LockBits(rect, ImageLockMode.WriteOnly, PixelFormat.Format32bppArgb);

            try
            {
                var dstStride = Math.Abs(dstData.Stride);
                var dstBytes = new byte[dstStride * outHeight];

                for (var y = 0; y < outHeight; y++)
                {
                    var sy = MapCoord(y, outHeight, srcHeight);
                    var dstRow = dstData.Stride >= 0 ? y * dstStride : (outHeight - 1 - y) * dstStride;
                    for (var x = 0; x < outWidth; x++)
                    {
                        var sx = MapCoord(x, outWidth, srcWidth);
                        var srcIdx = (sy * srcWidth) + sx;
                        var argb = srcArgb[srcIdx];

                        var dstIdx = dstRow + (x * 4);
                        dstBytes[dstIdx] = (byte)(argb & 0xFF); // B
                        dstBytes[dstIdx + 1] = (byte)((argb >> 8) & 0xFF); // G
                        dstBytes[dstIdx + 2] = (byte)((argb >> 16) & 0xFF); // R
                        dstBytes[dstIdx + 3] = 255;
                    }
                }

                Marshal.Copy(dstBytes, 0, dstData.Scan0, dstBytes.Length);
            }
            finally
            {
                bitmap.UnlockBits(dstData);
            }

            var dir = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(dir))
                dir = ".";
            Directory.CreateDirectory(dir);
            bitmap.Save(path, ImageFormat.Png);
        }

        private static int MapCoord(int coord, int outSize, int srcSize)
        {
            if (srcSize <= 1)
                return 0;
            var t = (coord + 0.5) / outSize;
            var mapped = (int)Math.Floor(t * srcSize);
            if (mapped < 0)
                return 0;
            if (mapped >= srcSize)
                return srcSize - 1;
            return mapped;
        }

        private static bool TrySetOpenGlAntialiasLevel(int level, out int previousLevel)
        {
            previousLevel = 0;

            try
            {
                previousLevel = (int)Rhino.ApplicationSettings.OpenGLSettings.AntialiasLevel;
                if (previousLevel == level)
                    return false;

                Rhino.ApplicationSettings.OpenGLSettings.AntialiasLevel = (Rhino.AntialiasLevel)level;
                return true;
            }
            catch
            {
                return false;
            }
        }
    }
}
