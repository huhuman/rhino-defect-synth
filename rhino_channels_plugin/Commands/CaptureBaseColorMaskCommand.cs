using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.Versioning;
using Rhino;
using Rhino.Commands;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.Geometry;
using Rhino.Input;

namespace RhinoChannelsPlugin.Commands
{
    [SupportedOSPlatform("windows")]
    public sealed class CaptureBaseColorMaskCommand : Command
    {
        private const bool VerboseLogging = false;

        private sealed class MaskObjectEntry : IDisposable
        {
            public MaskObjectEntry(Guid objectId, Color color, Mesh[] meshes)
            {
                ObjectId = objectId;
                Color = color;
                Meshes = meshes ?? Array.Empty<Mesh>();
            }

            public Guid ObjectId { get; }
            public Color Color { get; }
            public Mesh[] Meshes { get; }

            public void Dispose()
            {
                foreach (var mesh in Meshes)
                {
                    try
                    {
                        mesh?.Dispose();
                    }
                    catch
                    {
                        // Ignore mesh disposal failures during teardown.
                    }
                }
            }
        }

        private sealed class MaskCaptureConduit : DisplayConduit
        {
            private readonly Guid _viewportId;
            private readonly IReadOnlyList<MaskObjectEntry> _entries;
            private readonly HashSet<Guid> _objectIds;
            private readonly Color _backgroundColor;

            public MaskCaptureConduit(RhinoView view, IReadOnlyList<MaskObjectEntry> entries, Color backgroundColor)
            {
                _viewportId = view.ActiveViewport.Id;
                _entries = entries;
                _backgroundColor = backgroundColor;
                _objectIds = new HashSet<Guid>();
                foreach (var entry in entries)
                    _objectIds.Add(entry.ObjectId);
            }

            private bool IsTargetViewport(RhinoViewport viewport)
            {
                return viewport != null && viewport.Id == _viewportId;
            }

            protected override void ObjectCulling(CullObjectEventArgs e)
            {
                if (!IsTargetViewport(e.Viewport))
                    return;

                var rhinoObject = e.RhinoObject;
                if (rhinoObject != null && _objectIds.Contains(rhinoObject.Id))
                    e.CullObject = true;
            }

            protected override void PreDrawObjects(DrawEventArgs e)
            {
                if (!IsTargetViewport(e.Viewport))
                    return;

                e.Display.ClearFrameBuffer(_backgroundColor);
                e.Display.EnableLighting(false);
            }

            protected override void PostDrawObjects(DrawEventArgs e)
            {
                if (!IsTargetViewport(e.Viewport))
                    return;

                e.Display.EnableLighting(false);
                foreach (var entry in _entries)
                    DrawMaskObject(e.Display, entry);
            }
        }

        public override string EnglishName => "CaptureBaseColorMask";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
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

                var srcSize = view.ActiveViewport.Size;
                if (srcSize.Width <= 0 || srcSize.Height <= 0)
                    throw new InvalidOperationException("Viewport size is invalid.");

                var outWidth = width > 0 ? width : srcSize.Width;
                var outHeight = height > 0 ? height : srcSize.Height;
                if (outWidth <= 0 || outHeight <= 0)
                    throw new InvalidOperationException("Invalid output dimensions.");

                LogVerbose(
                    $"CaptureBaseColorMask: using view '{view.ActiveViewport.Name}', source={srcSize.Width}x{srcSize.Height}, output={outWidth}x{outHeight}.");

                var maskEntries = CollectVisibleMaskEntries(doc);
                LogVerbose($"CaptureBaseColorMask: visible mask objects={maskEntries.Count}.");

                var previousMode = view.ActiveViewport.DisplayMode;
                var previousGrid = view.ActiveViewport.ConstructionGridVisible;
                var previousPlane = view.ActiveViewport.ConstructionPlaneVisible;
                var previousConstructionAxes = view.ActiveViewport.ConstructionAxesVisible;
                var previousWorldAxes = view.ActiveViewport.WorldAxesVisible;
                var shadedMode = DisplayModeDescription.GetDisplayMode(DisplayModeDescription.ShadedId);
                if (shadedMode == null)
                    throw new InvalidOperationException("Built-in display mode 'Shaded' is unavailable.");

                var changedAa = TrySetOpenGlAntialiasLevel(0, out var prevAaLevel);
                try
                {
                    view.ActiveViewport.DisplayMode = shadedMode;
                    view.ActiveViewport.ConstructionGridVisible = false;
                    view.ActiveViewport.ConstructionPlaneVisible = false;
                    view.ActiveViewport.ConstructionAxesVisible = false;
                    view.ActiveViewport.WorldAxesVisible = false;
                    view.Redraw();
                    RhinoApp.Wait();

                    var conduit = new MaskCaptureConduit(view, maskEntries, Color.White);
                    try
                    {
                        conduit.Enabled = true;
                        using var bitmap = CaptureMaskBitmap(view, outWidth, outHeight);
                        WritePng(maskPath, bitmap);
                    }
                    finally
                    {
                        conduit.Enabled = false;
                    }
                }
                finally
                {
                    foreach (var entry in maskEntries)
                        entry.Dispose();
                    view.ActiveViewport.DisplayMode = previousMode;
                    view.ActiveViewport.ConstructionGridVisible = previousGrid;
                    view.ActiveViewport.ConstructionPlaneVisible = previousPlane;
                    view.ActiveViewport.ConstructionAxesVisible = previousConstructionAxes;
                    view.ActiveViewport.WorldAxesVisible = previousWorldAxes;
                    if (changedAa)
                        TrySetOpenGlAntialiasLevel(prevAaLevel, out _);
                    view.Redraw();
                }

                LogVerbose($"CaptureBaseColorMask: wrote '{maskPath}'.");
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

        private static List<MaskObjectEntry> CollectVisibleMaskEntries(RhinoDoc doc)
        {
            var entries = new List<MaskObjectEntry>();
            foreach (var obj in doc.Objects)
            {
                if (obj == null || obj.IsDeleted)
                    continue;
                if (!obj.Attributes.Visible)
                    continue;
                if (obj.ObjectType == ObjectType.Light || obj.ObjectType == ObjectType.Grip)
                    continue;

                var layerIndex = obj.Attributes.LayerIndex;
                if (layerIndex < 0 || layerIndex >= doc.Layers.Count)
                    continue;

                var layer = doc.Layers[layerIndex];
                if (layer == null || layer.IsDeleted || !layer.IsVisible)
                    continue;

                var meshes = BuildMonotoneMeshes(obj, layer.Color);
                if (meshes.Count == 0)
                    continue;
                entries.Add(new MaskObjectEntry(obj.Id, layer.Color, meshes.ToArray()));
            }

            return entries;
        }

        private static Bitmap CaptureMaskBitmap(RhinoView view, int width, int height)
        {
            var bitmap = DisplayPipeline.DrawToBitmap(view.ActiveViewport, width, height);
            if (bitmap == null)
                throw new InvalidOperationException("DisplayPipeline.DrawToBitmap returned no bitmap.");
            return bitmap;
        }

        private static void DrawMaskObject(DisplayPipeline display, MaskObjectEntry entry)
        {
            foreach (var mesh in entry.Meshes)
            {
                if (mesh == null)
                    continue;
                display.DrawMeshFalseColors(mesh);
            }
        }

        private static List<Mesh> BuildMonotoneMeshes(RhinoObject rhinoObject, Color color)
        {
            var meshes = new List<Mesh>();

            var renderMeshes = rhinoObject.GetMeshes(MeshType.Render);
            if (renderMeshes != null)
            {
                foreach (var mesh in renderMeshes)
                {
                    if (mesh == null)
                        continue;
                    var dup = mesh.DuplicateMesh();
                    if (dup == null)
                        continue;
                    ApplyMonotoneVertexColors(dup, color);
                    meshes.Add(dup);
                }
            }

            if (meshes.Count > 0)
                return meshes;

            var geometry = rhinoObject.Geometry;
            if (geometry == null)
                return meshes;

            switch (geometry)
            {
                case Mesh mesh:
                    {
                        var dup = mesh.DuplicateMesh();
                        if (dup != null)
                        {
                            ApplyMonotoneVertexColors(dup, color);
                            meshes.Add(dup);
                        }
                        break;
                    }
                case Brep brep:
                    AppendMeshesFromBrep(brep, color, meshes);
                    break;
                case Extrusion extrusion:
                    using (var brep = extrusion.ToBrep())
                    {
                        if (brep != null)
                            AppendMeshesFromBrep(brep, color, meshes);
                    }
                    break;
                case Surface surface:
                    using (var brep = Brep.CreateFromSurface(surface))
                    {
                        if (brep != null)
                            AppendMeshesFromBrep(brep, color, meshes);
                    }
                    break;
            }

            return meshes;
        }

        private static void AppendMeshesFromBrep(Brep brep, Color color, List<Mesh> meshes)
        {
            var generated = Mesh.CreateFromBrep(brep, MeshingParameters.FastRenderMesh);
            if (generated == null)
                return;

            foreach (var mesh in generated)
            {
                if (mesh == null)
                    continue;
                ApplyMonotoneVertexColors(mesh, color);
                meshes.Add(mesh);
            }
        }

        private static void ApplyMonotoneVertexColors(Mesh mesh, Color color)
        {
            mesh.VertexColors.Clear();
            for (var i = 0; i < mesh.Vertices.Count; i++)
                mesh.VertexColors.Add(color);
        }
        private static void WritePng(string path, Bitmap bitmap)
        {
            var dir = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(dir))
                dir = ".";
            Directory.CreateDirectory(dir);
            bitmap.Save(path, ImageFormat.Png);
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

        private static void LogVerbose(string message)
        {
            if (VerboseLogging)
                RhinoApp.WriteLine(message);
        }
    }
}
