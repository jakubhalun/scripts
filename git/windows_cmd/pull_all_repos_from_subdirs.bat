@echo off
for /D %%G in ("*") do (echo %%G) && (cd %%G) && (git pull origin) && (cd ..)
pause