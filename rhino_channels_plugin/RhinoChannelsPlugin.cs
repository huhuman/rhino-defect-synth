using Rhino.PlugIns;
using System;
using Rhino;
using Rhino.Commands;

namespace RhinoChannelsPlugin
{
    public sealed class RhinoChannelsPlugin : RenderPlugIn
    {
        public static RhinoChannelsPlugin? Instance { get; private set; }

        public RhinoChannelsPlugin()
        {
            Instance = this;
            Rhino.RhinoApp.WriteLine("RhinoChannelsPlugin loaded.");
        }

        protected override Rhino.Commands.Result Render(RhinoDoc doc, RunMode mode, bool fastPreview)
        {
            throw new NotImplementedException("Render is not implemented for channel capture.");
        }
    }
}
