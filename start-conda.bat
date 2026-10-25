@echo off
rem Start PhotoStory from the "photostory" Conda environment (create it first, see README).
rem Works from the Anaconda Prompt, or by double-clicking if Anaconda/Miniconda is in a usual place.
cd /d "%~dp0"
where conda >nul 2>nul
if errorlevel 1 (
  for %%D in ("%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "C:\ProgramData\miniconda3" "C:\ProgramData\anaconda3") do (
    if exist "%%~D\Scripts\activate.bat" call "%%~D\Scripts\activate.bat" "%%~D" & goto found
  )
  echo Couldn't find Conda. Open the Anaconda Prompt, go to this folder and run start-conda.bat there.
  pause
  exit /b 1
)
:found
call conda activate photostory || (echo The photostory environment doesn't exist yet. Run: conda env create -f environment.yml & pause & exit /b 1)
python -m photostory
pause
