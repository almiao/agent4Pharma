'use strict';
const path = require('node:path');
exports.name = 'pharma-sharp-native';
exports.apply = function(ctx) {
 const native = process.env.PHARMA_DSH_SHARP_NATIVE;
 const previous = process.dlopen;
 process.dlopen = function(module, filename, flags) {
   if (typeof filename === 'string' && /[\\/]sharp-win32-x64-0\.35\.5\.node$/.test(filename) && native) {
     return previous.call(this, module, path.resolve(native), flags);
   }
   return previous.call(this, module, filename, flags);
 };
 ctx.effect(() => () => { process.dlopen = previous; });
};
