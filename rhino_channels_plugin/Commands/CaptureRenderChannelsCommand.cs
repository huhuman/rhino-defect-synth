using System;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Text;
using Rhino;
using Rhino.Commands;
using Rhino.Input.Custom;
using Rhino.Render;
using Rhino.PlugIns;

namespace RhinoChannelsPlugin.Commands
{
    public sealed class CaptureRenderChannelsCommand : Command
    {
        public override string EnglishName => "CaptureRenderChannels";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            var depthPath = string.Empty;
            var normalPath = string.Empty;
            var viewName = string.Empty;
            var width = 0;
            var height = 0;

            var go = new GetOption();
            go.SetCommandPrompt("Capture depth/normal channels to PFM files");
            go.AddOptionString("DepthPath", ref depthPath);
            go.AddOptionString("NormalPath", ref normalPath);
            go.AddOptionString("View", ref viewName);
            go.AddOptionInteger("Width", ref width);
            go.AddOptionInteger("Height", ref height);

            while (true)
            {
                var res = go.Get();
                if (res == Rhino.Input.GetResult.Option)
                    continue;
                if (res == Rhino.Input.GetResult.Nothing)
                    break;
                if (res != Rhino.Input.GetResult.Cancel && res != Rhino.Input.GetResult.Nothing)
                    return Result.Cancel;
                return Result.Cancel;
            }

            if (string.IsNullOrWhiteSpace(depthPath) || string.IsNullOrWhiteSpace(normalPath))
                throw new ArgumentException("DepthPath and NormalPath are required.");

            var view = string.IsNullOrWhiteSpace(viewName)
                ? doc.Views.ActiveView
                : doc.Views.Find(viewName, false);

            if (view == null)
                throw new InvalidOperationException("No matching view found.");

            var size = view.ActiveViewport.Size;
            var targetWidth = width > 0 ? width : size.Width;
            var targetHeight = height > 0 ? height : size.Height;
            var rect = new Rectangle(0, 0, targetWidth, targetHeight);

            var pipeline = new ChannelCapturePipeline(doc, RhinoChannelsPlugin.Instance, new Size(targetWidth, targetHeight));
            var rw = pipeline.GetRenderWindow(view.ActiveViewport, false, rect);
            if (rw == null)
                throw new InvalidOperationException("Failed to acquire RenderWindow from pipeline.");

            rw.AddChannel(RenderWindow.StandardChannels.DistanceFromCamera);
            rw.AddChannel(RenderWindow.StandardChannels.NormalXYZ);

            pipeline.RenderWindow(view, rect, true);

            WriteDepth(rw, rect, depthPath);
            WriteNormal(rw, rect, normalPath);

            rw.Dispose();
            pipeline.Dispose();

            return Result.Success;
        }

        private static void WriteDepth(RenderWindow rw, Rectangle rect, string path)
        {
            var channel = rw.OpenChannel(RenderWindow.StandardChannels.DistanceFromCamera);
            if (channel == null)
                throw new InvalidOperationException("DistanceFromCamera channel unavailable.");

            var width = rect.Width;
            var height = rect.Height;
            var data = new float[width * height];
            channel.GetValues(rect, width, ComponentOrders.Irrelevant, data);
            channel.Dispose();

            WritePfm(path, width, height, 1, data);
        }

        private static void WriteNormal(RenderWindow rw, Rectangle rect, string path)
        {
            var channel = rw.OpenChannel(RenderWindow.StandardChannels.NormalXYZ);
            if (channel == null)
                throw new InvalidOperationException("NormalXYZ channel unavailable.");

            var width = rect.Width;
            var height = rect.Height;
            var data = new float[width * height * 3];
            channel.GetValues(rect, width * 3, ComponentOrders.XYZ, data);
            channel.Dispose();

            WritePfm(path, width, height, 3, data);
        }

        private static void WritePfm(string path, int width, int height, int channels, float[] data)
        {
            var dir = Path.GetDirectoryName(path);
            if (string.IsNullOrEmpty(dir))
                dir = ".";
            Directory.CreateDirectory(dir);

            var header = channels == 3
                ? string.Format(CultureInfo.InvariantCulture, "PF\n{0} {1}\n-1.0\n", width, height)
                : string.Format(CultureInfo.InvariantCulture, "Pf\n{0} {1}\n-1.0\n", width, height);

            using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read);
            using var bw = new BinaryWriter(fs, Encoding.ASCII, false);
            bw.Write(Encoding.ASCII.GetBytes(header));
            for (var i = 0; i < data.Length; i++)
                bw.Write(data[i]);
        }
    }

    internal sealed class ChannelCapturePipeline : RenderPipeline
    {
        public ChannelCapturePipeline(RhinoDoc doc, PlugIn plugin, Size size)
            : base(doc, RunMode.Scripted, plugin, size, "Channel Capture", RenderWindow.StandardChannels.RGBA)
        {
        }
    }
}
