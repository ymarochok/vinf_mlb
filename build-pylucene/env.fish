# Source from Fish before building or using PyLucene.
source /home/leonard/study/vinf/.venv-lucene/bin/activate.fish
set -gx JAVA_HOME /usr/lib/jvm/java-21-openjdk
fish_add_path --path --prepend "$JAVA_HOME/bin"
set -gx JCC_JDK "$JAVA_HOME"
set -gx JCC_INCLUDES "$JAVA_HOME/include:$JAVA_HOME/include/linux"
set -gx JCC_LFLAGS "-L$JAVA_HOME/lib/server:-Wl,-rpath,$JAVA_HOME/lib/server:-ljvm"
set -gx LD_LIBRARY_PATH "$JAVA_HOME/lib/server" $LD_LIBRARY_PATH
set -gx NO_SHARED 1
set -gx PYTHON "$VIRTUAL_ENV/bin/python"
set -gx JCC "$PYTHON -m jcc"
set -gx NUM_FILES 16
set -gx MODERN_PACKAGING true
