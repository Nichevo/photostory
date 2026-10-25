@echo off
setlocal
rem Start PhotoStory from its Conda environment (create it first, see README).
rem Works from the Anaconda Prompt, or by double-click. Conda and this folder can be on any drive.
rem
rem If Conda isn't found, or your environment has another name or lives in a folder of its own,
rem set these two lines, for example:
rem   set "CONDA_ROOT=E:\miniconda3"
rem   set "PHOTOSTORY_ENV=D:\envs\photostory"
set "CONDA_ROOT="
set "PHOTOSTORY_ENV=photostory"

cd /d "%~dp0"

rem Already inside an Anaconda Prompt?
where conda >nul 2>nul && goto activate

rem Conda tells us where it lives when it has been set up in this window or system-wide.
if defined CONDA_EXE for %%F in ("%CONDA_EXE%") do if not defined CONDA_ROOT set "CONDA_ROOT=%%~dpF.."

if not defined CONDA_ROOT (
  for %%R in ("%USERPROFILE%" "%LOCALAPPDATA%" "%ProgramData%" C:\ D:\ E:\ F:\ G:\ C:\ProgramData D:\ProgramData E:\ProgramData) do (
    for %%N in (miniconda3 anaconda3 Miniconda3 Anaconda3 miniforge3 mambaforge conda) do (
      if not defined CONDA_ROOT if exist "%%~R\%%N\Scripts\activate.bat" set "CONDA_ROOT=%%~R\%%N"
    )
  )
)
if not defined CONDA_ROOT (
  echo Couldn't find Conda. Either open the Anaconda Prompt, go to this folder and run start-conda.bat,
  echo or edit this file and set CONDA_ROOT to your Conda folder, e.g. E:\miniconda3
  pause
  exit /b 1
)
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ROOT%"

:activate
call conda activate "%PHOTOSTORY_ENV%"
if errorlevel 1 (
  echo Couldn't open the Conda environment "%PHOTOSTORY_ENV%".
  echo Create it with:  conda env create -f environment.yml
  echo or, if yours has another name or folder, set PHOTOSTORY_ENV at the top of this file.
  pause
  exit /b 1
)
python -m photostory
pause
