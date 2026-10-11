// Independent host contract for executing the shipped JXA script on non-macOS CI.
// This does NOT validate Apple's JXA bridge or a real NSPasteboard.
const config = JSON.parse(process.env.XDOG_NATIVE_PEER);
const calls = [];
const nil = {isNil: () => true};
function data(encoded, length) {
    return {
        isNil: () => false,
        length: length === undefined ? Buffer.from(encoded, 'base64').length : length,
        base64EncodedStringWithOptions: options => {
            calls.push(['base64', options]);
            return encoded;
        }
    };
}
function $(value) { return value; }
const board = {
    changeCount: 7,
    types: config.types || [],
    dataForType: type => {
        calls.push(['read', type]);
        if (config.changed) board.changeCount++;
        if (config.missing) return nil;
        return data(config.bytes || '', config.length);
    },
    stringForType: type => {
        calls.push(['text', type]);
        if (config.changed) board.changeCount++;
        if (config.text === undefined) return nil;
        return {
            isNil: () => false,
            length: config.text.length,
            dataUsingEncoding: encoding => {
                calls.push(['utf8', encoding]);
                return data(Buffer.from(config.text).toString('base64'), config.length);
            }
        };
    }
};
$.NSPasteboard = {generalPasteboard: board};
$.NSUTF8StringEncoding = 4;
$.NSBitmapImageFileTypePNG = 4;
$.NSBitmapImageRep = {
    imageRepWithData: source => {
        calls.push(['bitmap', source.length]);
        if (config.invalidBitmap) return nil;
        return {
            isNil: () => false,
            pixelsWide: config.width === undefined ? 1 : config.width,
            pixelsHigh: config.height === undefined ? 1 : config.height,
            representationUsingTypeProperties: (type, properties) => {
                calls.push(['png', type, properties]);
                return config.invalidConversion ? nil : data(config.png || '', config.convertedLength);
            }
        };
    }
};
const ObjC = {
    import: name => calls.push(['import', name]),
    unwrap: value => value,
    deepUnwrap: value => value
};
