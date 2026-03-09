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
            public LayerMaskEntry(int index, Color color, string name, Guid[] objectIds)
            {
                Index = index;
                Color = color;
                Name = name;
                ObjectIds = objectIds;
            }

            public int Index { get; }
            public Color Color { get; }
            public string Name { get; }
            public Guid[] ObjectIds { get; }
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

                var objectVisibilitySnapshot = SnapshotObjectVisibility(doc);
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
                    var initiallyVisibleObjectIds = new List<Guid>();
                    foreach (var pair in objectVisibilitySnapshot)
                    {
                        if (pair.Value)
                            initiallyVisibleObjectIds.Add(pair.Key);
                    }

                    foreach (var layer in maskLayers)
                    {
                        SetOnlyObjectsVisible(doc, initiallyVisibleObjectIds, layer.ObjectIds);
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
                    RestoreObjectVisibility(doc, objectVisibilitySnapshot);
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

        private static Dictionary<Guid, bool> SnapshotObjectVisibility(RhinoDoc doc)
        {
            var snapshot = new Dictionary<Guid, bool>();
            foreach (var obj in doc.Objects)
            {
                if (obj == null || obj.IsDeleted)
                    continue;
                snapshot[obj.Id] = obj.Attributes.Visible;
            }
            return snapshot;
        }

        private static void RestoreObjectVisibility(RhinoDoc doc, IReadOnlyDictionary<Guid, bool> snapshot)
        {
            foreach (var pair in snapshot)
            {
                var obj = doc.Objects.FindId(pair.Key);
                if (obj == null || obj.IsDeleted)
                    continue;
                SetObjectVisible(doc, pair.Key, pair.Value);
            }
        }

        private static List<LayerMaskEntry> CollectVisibleLayersWithObjects(RhinoDoc doc)
        {
            var objectIdsByLayer = new Dictionary<int, List<Guid>>();
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

                if (!objectIdsByLayer.TryGetValue(layerIndex, out var ids))
                {
                    ids = new List<Guid>();
                    objectIdsByLayer[layerIndex] = ids;
                }
                ids.Add(obj.Id);
            }

            var layers = new List<LayerMaskEntry>();
            for (var i = 0; i < doc.Layers.Count; i++)
            {
                if (!objectIdsByLayer.TryGetValue(i, out var ids))
                    continue;

                var layer = doc.Layers[i];
                if (layer == null || layer.IsDeleted || !layer.IsVisible)
                    continue;

                var name = string.IsNullOrWhiteSpace(layer.FullPath)
                    ? (string.IsNullOrWhiteSpace(layer.Name) ? $"Layer_{i}" : layer.Name)
                    : layer.FullPath;
                layers.Add(new LayerMaskEntry(i, layer.Color, name, ids.ToArray()));
            }

            return layers;
        }

        private static void SetOnlyObjectsVisible(
            RhinoDoc doc,
            IReadOnlyList<Guid> candidateObjectIds,
            IReadOnlyList<Guid> targetVisibleObjectIds)
        {
            var targetSet = new HashSet<Guid>(targetVisibleObjectIds);
            foreach (var objectId in candidateObjectIds)
            {
                var shouldBeVisible = targetSet.Contains(objectId);
                SetObjectVisible(doc, objectId, shouldBeVisible);
            }
        }

        private static void SetObjectVisible(RhinoDoc doc, Guid objectId, bool visible)
        {
            var obj = doc.Objects.FindId(objectId);
            if (obj == null || obj.IsDeleted)
                return;
            if (obj.Attributes.Visible == visible)
                return;

            var attrs = obj.Attributes.Duplicate();
            attrs.Visible = visible;
            doc.Objects.ModifyAttributes(objectId, attrs, true);
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
