@echo off
rem Double-click me. Runs start-mtgdeckbuilder.ps1 without having to change the PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-mtgdeckbuilder.ps1"
