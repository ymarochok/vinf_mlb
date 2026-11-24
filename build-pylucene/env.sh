# Source this file before building or using PyLucene in this project.
source /home/leonard/study/vinf/.venv-lucene/bin/activate
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
export PATH="$JAVA_HOME/bin:$PATH"
export JCC_JDK="$JAVA_HOME"
export JCC_INCLUDES="$JAVA_HOME/include:$JAVA_HOME/include/linux"
export JCC_LFLAGS="-L$JAVA_HOME/lib/server:-Wl,-rpath,$JAVA_HOME/lib/server:-ljvm"
export LD_LIBRARY_PATH="$JAVA_HOME/lib/server${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export NO_SHARED=1
export PYTHON="$VIRTUAL_ENV/bin/python"
export JCC="$PYTHON -m jcc"
export NUM_FILES=16
export MODERN_PACKAGING=true
