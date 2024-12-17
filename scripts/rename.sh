#!/bin/sh

BUILD_DIR=caterva2/services/static/build
MANIFEST=$BUILD_DIR/manifest.json

hash() {
    local srcname="$1"                          # main.js
    local srcpath=$BUILD_DIR/$srcname           # var/build/main.js
    local hash=$(md5sum "${srcpath}"|cut -c-8)  # 37b383e0
    local name="${srcname%.*}"                  # main
    local ext=${srcname##*.}                    # js
    dst="${name}.${hash}.${ext}"                # main.37b383e0.js
    mv $srcpath $BUILD_DIR/$dst
}

# Rename main.js to main.<hash>.js and update manifest.json
hash main.min.js
contents=$(jq --arg dst "$dst" '."src/main.js".file = $dst' $MANIFEST) && echo -E "${contents}" > $MANIFEST

# Rename style.css to style.<hash>.css and update manifest.json
hash style.css
contents=$(jq --arg dst "$dst" '."src/main.js".css = [$dst]' $MANIFEST) && echo -E "${contents}" > $MANIFEST
