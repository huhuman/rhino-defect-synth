using Rhino.PlugIns;

namespace RhinoChannelsPlugin
{
    public sealed class RhinoChannelsPlugin : RenderPlugIn
    {
        public static RhinoChannelsPlugin Instance { get; private set; }

        public RhinoChannelsPlugin()
        {
            Instance = this;
        }
    }
}
