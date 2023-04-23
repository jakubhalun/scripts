@echo off
for /D %%G in ("*") do (
    echo %%G
    cd %%G
    if exist ".git" (
        git pull
    )
    cd ..
)
pause
