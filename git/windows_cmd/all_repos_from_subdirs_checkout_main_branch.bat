@echo off
for /D %%G in ("*") do (echo %%G) && (cd %%G) && (git checkout master) && (git branch) && (cd ..)
pause