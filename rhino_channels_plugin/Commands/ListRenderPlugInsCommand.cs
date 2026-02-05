using System;
using System.Reflection;
using Rhino;
using Rhino.Commands;
using Rhino.PlugIns;

namespace RhinoChannelsPlugin.Commands
{
    public sealed class ListRenderPlugInsCommand : Command
    {
        public override string EnglishName => "ListRenderPlugIns";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            RhinoApp.WriteLine("ListRenderPlugIns: listing render plug-ins...");

            var plugInType = typeof(PlugIn);
            var listMethod = plugInType.GetMethod("GetPlugInList", BindingFlags.Public | BindingFlags.Static)
                           ?? plugInType.GetMethod("GetInstalledPlugIns", BindingFlags.Public | BindingFlags.Static);

            if (listMethod != null)
            {
                RhinoApp.WriteLine($"ListRenderPlugIns: using {listMethod.Name}()");
                var listObj = listMethod.Invoke(null, null) as Array;
                if (listObj != null)
                {
                    RhinoApp.WriteLine($"ListRenderPlugIns: list count={listObj.Length}");
                    foreach (var item in listObj)
                    {
                        RhinoApp.WriteLine($"ListRenderPlugIns: item type={item?.GetType().FullName ?? "<null>"}");
                        var plugin = item as PlugIn;
                        if (plugin is RenderPlugIn render)
                            RhinoApp.WriteLine($"RenderPlugIn: {render.Name} | {render.Id}");
                    }
                }
            }
            else
            {
                RhinoApp.WriteLine("ListRenderPlugIns: no PlugIn list method found.");
            }

            RhinoApp.WriteLine("ListRenderPlugIns: fallback to type scan.");
            var renderType = typeof(RenderPlugIn);
            var count = 0;
            foreach (var type in renderType.Assembly.GetTypes())
            {
                if (!renderType.IsAssignableFrom(type) || type.IsAbstract)
                    continue;

                var idProp = type.GetProperty("PlugInId", BindingFlags.Public | BindingFlags.Static)
                            ?? type.GetProperty("Id", BindingFlags.Public | BindingFlags.Static);
                var idVal = idProp?.GetValue(null, null);
                if (idVal is Guid guid)
                {
                    RhinoApp.WriteLine($"RenderPlugIn: {type.FullName} | {guid}");
                }
                else
                {
                    RhinoApp.WriteLine($"RenderPlugIn: {type.FullName}");
                }
                count++;
            }
            RhinoApp.WriteLine($"ListRenderPlugIns: type scan found {count} render plug-in types.");

            return Result.Success;
        }
    }
}
