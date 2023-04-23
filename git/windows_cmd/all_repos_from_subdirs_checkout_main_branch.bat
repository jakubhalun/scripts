@echo off
for /D %%G in ("*") do (
    echo %%G
    cd %%G
    if exist ".git" (
        git checkout master || git checkout main
        git branch
    )
    cd ..
)
pause
