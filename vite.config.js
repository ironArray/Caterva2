import { resolve } from 'path'
import { defineConfig } from 'vite'

// https://vitejs.dev/config/
export default defineConfig(({ command, mode }) => {
    const main = resolve(__dirname, 'src/main.js')

    let config = {
        base: '/static/',
        build: {
            cssCodeSplit: true,
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
            input: {main}
        }
    }
    else { // build
        config.build.lib = {
            entry: {main},
            formats: ['es'],
            fileName: (format, entryName) => `${entryName}.js`,
        }
        // In lib mode filenames don't include a hash by default, add one here
        config.build.rollupOptions.output = {
            entryFileNames: '[name]-[hash].js',
            assetFileNames: '[name]-[hash][extname]',
        }
    }

    //console.log('CONFIG', config);
    return config;
})
