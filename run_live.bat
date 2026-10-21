@echo off
rem Live CodeViz window.  Usage:  run_live.bat [file.py]   (default: demo.py)
set BLENDER="C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
set HERE=%~dp0
set SRC=%~f1
if "%~1"=="" set SRC=%HERE%demo.py
rem Opening a .blend skips Blender's splash screen; make an empty one once.
if not exist "%HERE%codeviz.blend" %BLENDER% -b --factory-startup --python-expr "import bpy; bpy.ops.wm.save_as_mainfile(filepath=r'%HERE%codeviz.blend')"
start "" %BLENDER% "%HERE%codeviz.blend" --python "%HERE%blender_live.py" -- "%SRC%"
