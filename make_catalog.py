#!/usr/bin/env python3
"""
BF6 Portal object catalog maker.

Builds a one-file HTML catalog of every object the Portal SDK allows on one map, with a clay render and the size
of each placeable model, searchable and filterable. Runs entirely on your PC with the Portal SDK's own Godot.
Standard Python 3.9+ only - nothing to install.

Usage: double-click the script and pick a map, or from a Command Prompt:
    python make_catalog.py MP_Portal_Sand
    python make_catalog.py MP_Abbasid --name "Siege of Cairo"
    python make_catalog.py --list                      (show every map name)

Options:
    --name "Nice Name"   map name shown on the page (default: a built-in name for the map)
    --sdk PATH           Portal SDK folder (default: the folder this script is in or above, else C:\\Program Files\\PortalSDK)
    --out FILE.html      where to save the page (default: a "catalogs" folder next to this script)
    --godot EXE          Godot to render with (default: the Godot exe in the SDK folder)
    --work DIR           scratch folder (default: %TEMP%\\bf6_catalog\\<map>); delete it when done
    --keep-going         re-use thumbnails already in the scratch folder (resume an interrupted run)

Godot opens a small window for each batch of 200 models while it renders; leave it alone until the script finishes.
A big map takes a few minutes.
"""
import argparse, base64, glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zlib

MAP_NAMES = {
    'MP_Abbasid': 'Siege of Cairo', 'MP_Aftermath': 'Empire State', 'MP_Aftermath_Portal': 'Empire State (Portal)',
    'MP_Atoll': 'Atoll', 'MP_Badlands': 'Blackwell Fields', 'MP_Battery': 'Iberian Offensive',
    'MP_Capstone': 'Liberation Peak', 'MP_Contaminated': 'Contaminated', 'MP_Dumbo': 'Manhattan Bridge',
    'MP_Eastwood': 'Eastwood', 'MP_FireStorm': 'Operation Firestorm', 'MP_GolmudRailway': 'Golmud Railway',
    'MP_Isolated': 'Tsuru Reef', 'MP_Limestone': 'Saints Quarter', 'MP_Outskirts': 'New Sobek City',
    'MP_Plaza': 'Plaza', 'MP_Portal_Ocean': 'Portal Ocean', 'MP_Portal_Sand': 'Portal Sandbox',
    'MP_Subsurface': 'Subsurface', 'MP_Tungsten': 'Mirak Valley',
    'MP_Granite_ClubHouse_Portal': 'Granite Clubhouse', 'MP_Granite_MainStreet_Portal': 'Granite Main Street',
    'MP_Granite_Marina_Portal': 'Granite Marina', 'MP_Granite_MilitaryRnD_Portal': 'Granite Military R&D',
    'MP_Granite_MilitaryStorage_Portal': 'Granite Military Storage', 'MP_Granite_TechCampus_Portal': 'Granite Tech Campus',
    'MP_Granite_Underground_Portal': 'Granite Underground',
}
# Which RuntimeSpawn_* list says "a script can spawn this here". Entries marked ? are best guesses.
RUNTIME_ENUM = {
    'MP_Abbasid': 'Abbasid', 'MP_Aftermath': 'Aftermath', 'MP_Aftermath_Portal': 'Aftermath', 'MP_Atoll': 'Atoll',
    'MP_Badlands': 'Badlands', 'MP_Battery': 'Battery', 'MP_Capstone': 'Capstone', 'MP_Contaminated': 'Contaminated',
    'MP_Dumbo': 'Dumbo', 'MP_Eastwood': 'Eastwood', 'MP_FireStorm': 'FireStorm', 'MP_GolmudRailway': 'GolmudRailway',
    'MP_Isolated': 'Isolated', 'MP_Limestone': 'Limestone', 'MP_Outskirts': 'Outskirts', 'MP_Plaza': 'Plaza',
    'MP_Portal_Ocean': 'Ocean', 'MP_Portal_Sand': 'Sand', 'MP_Subsurface': 'Subsurface', 'MP_Tungsten': 'Tungsten',
    'MP_Granite_Marina_Portal': 'Granite_Marina', 'MP_Granite_MilitaryRnD_Portal': 'Granite_MilitaryRnD',
    'MP_Granite_MilitaryStorage_Portal': 'Granite_MilitaryStorage', 'MP_Granite_Underground_Portal': 'Granite_Underground',
    'MP_Granite_MainStreet_Portal': 'Granite_Downtown',         # ?
    'MP_Granite_TechCampus_Portal': 'Granite_TechCenter',       # ?
    'MP_Granite_ClubHouse_Portal': 'Granite_ResidentialNorth',  # ?
}
PICTURED = {'props', 'nature', 'architecture', 'uncategorized', 'backdrop', 'lightfixtures'}
DIRMAP = {'audio': 'audio', 'fx': 'fx', 'props': 'props', 'architecture': 'architecture', 'nature': 'nature',
          'lightfixtures': 'lightfixtures', 'backdrop': 'backdrop', 'entities': 'entities'}
BATCH = 200

# Godot script: loads each model, frames it, saves a 176 px WebP and logs its size.
THUMB_GD = r'''extends SceneTree
var root3d: Node3D
var cam: Camera3D
func _aabb(n: Node, xf: Transform3D, acc: Array):
	var t = xf
	if n is Node3D: t = xf * n.transform
	if n is MeshInstance3D and n.mesh != null: acc.append(t * n.mesh.get_aabb())
	for c in n.get_children(): _aabb(c, t, acc)
func _initialize():
	root3d = Node3D.new(); get_root().add_child(root3d)
	var env = WorldEnvironment.new(); var e = Environment.new()
	e.background_mode = Environment.BG_COLOR; e.background_color = Color(0.91,0.93,0.95)
	e.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR; e.ambient_light_color = Color(1,1,1); e.ambient_light_energy = 0.35
	env.environment = e; root3d.add_child(env)
	var sun = DirectionalLight3D.new(); sun.rotation_degrees = Vector3(-55, 40, 0); sun.light_energy = 1.35; root3d.add_child(sun)
	var fill = DirectionalLight3D.new(); fill.rotation_degrees = Vector3(-20, -140, 0); fill.light_energy = 0.35; root3d.add_child(fill)
	cam = Camera3D.new(); root3d.add_child(cam); cam.current = true
	get_root().size = Vector2i(256, 256)
	_run()
func _run():
	var args = OS.get_cmdline_user_args()
	var names = FileAccess.get_file_as_string(args[0]).split("\n", false)
	var outdir = args[1]
	var mat = StandardMaterial3D.new(); mat.albedo_color = Color(0.72,0.68,0.6); mat.roughness = 0.9; mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	var log = FileAccess.open(outdir + "/_log_" + args[0].get_file() + ".txt", FileAccess.WRITE)
	for i in 6: await process_frame
	for line in names:
		var parts = line.split("\t")
		var nm = parts[0]; var f = parts[1]
		var res = ResourceLoader.load("res://models/" + f)
		if res == null:
			log.store_line("%s\tMISSING" % nm); continue
		var sub = res.instantiate(); root3d.add_child(sub)
		for mi in sub.find_children("*", "MeshInstance3D", true, false): mi.material_override = mat
		var acc = []; _aabb(sub, Transform3D.IDENTITY, acc)
		if acc.size() == 0:
			log.store_line("%s\tNOMESH" % nm); sub.queue_free(); await process_frame; continue
		var bb: AABB = acc[0]
		for a in acc: bb = bb.merge(a)
		var c = bb.get_center(); var r = max(bb.size.length() * 0.5, 0.05)
		cam.projection = Camera3D.PROJECTION_ORTHOGONAL; cam.size = r * 2.05; cam.far = 10000; cam.near = 0.01
		cam.position = c + Vector3(0.85, 0.6, 1.0).normalized() * (r * 3 + 5)
		cam.look_at(c, Vector3.UP)
		await process_frame; await process_frame; await RenderingServer.frame_post_draw
		var img = get_root().get_texture().get_image()
		img.resize(176, 176, Image.INTERPOLATE_LANCZOS)
		img.save_webp(outdir + "/" + nm + ".webp", true, 0.62)
		log.store_line("%s\tOK\t%.2f\t%.2f\t%.2f" % [nm, bb.size.x, bb.size.y, bb.size.z])
		sub.queue_free(); await process_frame
	log.close()
	quit()
'''

TEMPLATE_Z = (
    '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'
    'tX4z+b8uswLm')


def find_sdk(arg):
    if arg:
        return arg
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        if os.path.exists(os.path.join(d, 'FbExportData', 'asset_types.json')):
            return d
    return r'C:\Program Files\PortalSDK'


def find_godot(sdk, arg):
    if arg:
        return arg
    found = sorted(glob.glob(os.path.join(sdk, 'Godot*.exe'))) or sorted(glob.glob(os.path.join(sdk, 'Godot*x86_64')))
    if not found:
        sys.exit('Could not find Godot in %s - pass --godot "path\\to\\Godot.exe"' % sdk)
    return found[0]


def scn_name(mesh):
    # Godot names imported models <file>.glb-<md5 of "res://raw/models/<file>.glb">.scn
    return '%s.glb-%s.scn' % (mesh, hashlib.md5(('res://raw/models/%s.glb' % mesh).encode()).hexdigest())


def object_folders(sdk):
    # object name -> its folder under GodotProject/objects (for the res:// path and the category)
    root = os.path.join(sdk, 'GodotProject', 'objects')
    out = {}
    for p in glob.glob(os.path.join(root, '**', '*.tscn'), recursive=True):
        rel = os.path.relpath(os.path.dirname(p), root).replace('\\', '/')
        out.setdefault(os.path.splitext(os.path.basename(p))[0], rel)
    return out


def main():
    ap = argparse.ArgumentParser(description='Make a BF6 Portal object catalog page for one map.')
    ap.add_argument('map', nargs='?', help='map level name, e.g. MP_Portal_Sand (use --list to see them)')
    ap.add_argument('--name'); ap.add_argument('--sdk'); ap.add_argument('--out'); ap.add_argument('--godot')
    ap.add_argument('--work'); ap.add_argument('--keep-going', action='store_true'); ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    sdk = find_sdk(a.sdk)
    try:
        levels = list(json.load(open(os.path.join(sdk, 'FbExportData', 'level_info.json'))))
    except OSError:
        levels = sorted(MAP_NAMES)
    if a.list:
        for lv in levels:
            print('  %-36s %s' % (lv, MAP_NAMES.get(lv, '')))
        return
    if not a.map:
        # Started without a map (for example by double-clicking): ask for one.
        print('BF6 Portal object catalog maker\nSDK: %s\n' % sdk)
        for n, lv in enumerate(levels, 1):
            print('  %2d  %-36s %s' % (n, lv, MAP_NAMES.get(lv, '')))
        choice = input('\nType a number or a map code (Enter to quit): ').strip()
        if not choice:
            return
        a.map = levels[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(levels) else choice
    level = a.map
    name = a.name or MAP_NAMES.get(level, level)
    slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalogs', slug + '-object-catalog.html')
    work = a.work or os.path.join(tempfile.gettempdir(), 'bf6_catalog', level)
    imported = os.path.join(sdk, 'GodotProject', '.godot', 'imported')
    godot = find_godot(sdk, a.godot)

    # 1. Which objects are allowed on this map
    assets = json.load(open(os.path.join(sdk, 'FbExportData', 'asset_types.json')))['AssetTypes']
    if not any(level in (x.get('levelRestrictions') or []) for x in assets) and level not in MAP_NAMES:
        sys.exit('Unknown map "%s" - run with --list to see the names.' % level)
    folders = object_folders(sdk)
    items = []
    for x in assets:
        r = x.get('levelRestrictions') or []
        if r and level not in r:
            continue
        k = {c['name']: c['value'] for c in x.get('constants', [])}
        d = x.get('directory', '')
        folder = folders.get(x['type'])
        if folder:
            cat = folder.split('/')[0]
        else:
            cat = 'gameplay' if d.lower().startswith('gameplay') else DIRMAP.get(d.split('/')[-1].lower(), 'uncategorized')
        items.append({'name': x['type'], 'cat': cat, 'dir': d, 'folder': folder,
                      'scn': scn_name(k['mesh']) if k.get('mesh') else None})
    print('%s (%s): %d objects allowed' % (name, level, len(items)))

    # 2. Render the placeable models with Godot
    proj = os.path.join(work, 'godotproj'); thumbs = os.path.join(work, 'thumbs')
    if not a.keep_going and os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(os.path.join(proj, 'models'), exist_ok=True); os.makedirs(thumbs, exist_ok=True)
    with open(os.path.join(proj, 'project.godot'), 'w') as f:
        f.write('config_version=5\n[application]\nconfig/name="thumbs"\n')
    with open(os.path.join(proj, 'thumb.gd'), 'w', newline='\n') as f:
        f.write(THUMB_GD)
    todo, missing = [], 0
    for i in items:
        if i['cat'] not in PICTURED or not i['scn']:
            continue
        src = os.path.join(imported, i['scn'])
        if not os.path.exists(src):
            missing += 1; continue
        dst = os.path.join(proj, 'models', i['scn'])
        if not os.path.exists(dst):
            shutil.copy(src, dst)
        if not os.path.exists(os.path.join(thumbs, i['name'] + '.webp')):
            todo.append('%s\t%s' % (i['name'], i['scn']))
    if missing:
        print('  %d models were not found in GodotProject\\.godot\\imported (open the project in Godot once to import them)' % missing)
    batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
    print('Rendering %d models in %d batch(es) with %s' % (len(todo), len(batches), os.path.basename(godot)))
    for n, b in enumerate(batches):
        lst = os.path.join(work, 'todo_%02d.txt' % n)
        with open(lst, 'w', newline='\n') as f:
            f.write('\n'.join(b) + '\n')
        cmd = [godot, '--path', proj, '--audio-driver', 'Dummy', '--script', 'thumb.gd', '--', lst, thumbs]
        if not sys.platform.startswith('win') and shutil.which('xvfb-run') and not os.environ.get('DISPLAY'):
            cmd = ['xvfb-run', '-a', '-s', '-screen 0 1024x768x24'] + cmd[:1] + ['--rendering-driver', 'opengl3'] + cmd[1:]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600)
        print('  batch %d of %d done' % (n + 1, len(batches)))

    # 3. Build the page
    sizes = {}
    for f in glob.glob(os.path.join(thumbs, '_log_*')):
        for line in open(f):
            p = line.rstrip('\n').split('\t')
            if len(p) == 5 and p[1] == 'OK':
                sizes[p[0]] = [round(float(v), 1) for v in p[2:]]
    dts = open(os.path.join(sdk, 'code', 'types', 'mod', 'index.d.ts'), encoding='utf-8').read()
    enums = {m.group(1): set(re.findall(r'(\w+),', m.group(2)))
             for m in re.finditer(r'export enum RuntimeSpawn_(\w+) \{([^}]*)\}', dts)}
    spawnable = set(enums.get('Common', set()))
    if RUNTIME_ENUM.get(level) in enums:
        spawnable |= enums[RUNTIME_ENUM[level]]
    else:
        print('  Note: no map-specific spawn list found for %s; "From script" only covers common objects' % level)
    data = []
    for i in items:
        if i['cat'] == 'gameplay' and not i['folder']:
            sub = i['dir'].split('/')[1].lower() if '/' in i['dir'] else ''
            path = 'res://objects/gameplay/%s%s.tscn' % (sub + '/' if sub else '', i['name'])
        else:
            path = 'res://objects/%s/%s.tscn' % (i['folder'] or i['cat'], i['name'])
        rec = {'n': i['name'], 'c': i['cat'], 'd': i['dir'], 'p': path, 's': 1 if i['name'] in spawnable else 0}
        if i['name'] in sizes:
            rec['z'] = sizes[i['name']]
        webp = os.path.join(thumbs, i['name'] + '.webp')
        if os.path.exists(webp):
            rec['i'] = base64.b64encode(open(webp, 'rb').read()).decode()
        data.append(rec)
    page = zlib.decompress(base64.b64decode(TEMPLATE_Z)).decode('utf-8')
    page = (page.replace('__TITLE__', '%s Objects' % name).replace('__H1__', '%s Object Catalog' % name)
                .replace('__MAPID__', level).replace('__MAPNAME__', name))
    page = page.replace('__DATA__', json.dumps(data, separators=(',', ':')).replace('</', '<\\/'))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(page)
    pics = sum(1 for d in data if d.get('i'))
    print('Done: %d objects, %d with pictures -> %s (%.1f MB)' % (len(data), pics, out, os.path.getsize(out) / 1e6))
    print('Scratch files are in %s (safe to delete).' % work)


if __name__ == '__main__':
    # When Windows opened a fresh console for this script (double-click), keep it open at the end.
    double_clicked = len(sys.argv) == 1
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code)
    except Exception:
        import traceback
        traceback.print_exc()
        print('\nSomething went wrong - the message above says what. Include it if you report a problem.')
    if double_clicked:
        try:
            input('\nPress Enter to close this window.')
        except EOFError:
            pass
