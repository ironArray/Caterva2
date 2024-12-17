import { defineConfig } from 'vite'

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
    let entry = 'src/main.js';
    let config = {
        base: '/static/',
        build: {
            manifest: 'manifest.json',
            outDir: 'caterva2/services/static/build',
            rollupOptions: {
            }
        },
        plugins: [
        ]
    }

    if (command === 'serve') {
        config.build.rollupOptions = {
            input: {
                main: entry,
            }
        }
    } else { // build
        config.build.lib = {
            entry,
            formats: ['es'],
            fileName: (format) => 'main.js',
        }
    }

    //console.log('CONFIG', config);
    return config;
})
