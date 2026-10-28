import type { UwbMap } from '@/types/uwb';

const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
  for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
  return value >>> 0;
});

function crc32(bytes: Uint8Array, start = 0, end = bytes.length) {
  let crc = 0xffffffff;
  for (let i = start; i < end; i++) crc = crcTable[(crc ^ bytes[i]) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** Check the complete PNG before handing it to a platform image decoder. CRC is for corruption, not authentication. */
export function checkMapImage(bg: UwbMap['background']) {
  const fail = () => {
    throw new Error('底图数据不完整或校验失败，请重新加载');
  };
  if (
    !bg ||
    !Number.isInteger(bg.width) ||
    !Number.isInteger(bg.height) ||
    bg.width < 1 ||
    bg.height < 1 ||
    bg.width > 10000 ||
    bg.height > 10000 ||
    bg.width * bg.height > 20_000_000 ||
    typeof bg.imageDataUri !== 'string' ||
    !bg.imageDataUri.startsWith('data:image/png;base64,')
  )
    return fail();
  const encoded = bg.imageDataUri.slice(22);
  if (
    encoded.length > 5_333_336 ||
    encoded.length % 4 !== 0 ||
    !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)
  )
    return fail();
  let binary: string;
  try {
    binary = atob(encoded);
  } catch {
    return fail();
  }
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  if (bytes.length < 57 || bytes.length > 4_000_000) return fail();
  const view = new DataView(bytes.buffer);
  if (view.getUint32(0) !== 0x89504e47 || view.getUint32(4) !== 0x0d0a1a0a) return fail();
  let offset = 8;
  let hasData = false;
  let hasEnd = false;
  while (offset + 12 <= bytes.length) {
    const length = view.getUint32(offset);
    const end = offset + 8 + length;
    if (end + 4 > bytes.length || crc32(bytes, offset + 4, end) !== view.getUint32(end))
      return fail();
    const type = view.getUint32(offset + 4);
    if (offset === 8) {
      if (
        type !== 0x49484452 ||
        length !== 13 ||
        view.getUint32(offset + 8) !== bg.width ||
        view.getUint32(offset + 12) !== bg.height
      )
        return fail();
    } else if (type === 0x49484452) return fail();
    if (type === 0x49444154) hasData = true;
    offset = end + 4;
    if (type === 0x49454e44) {
      hasEnd = length === 0 && offset === bytes.length;
      break;
    }
  }
  if (!hasData || !hasEnd) return fail();
  const checksum = crc32(bytes).toString(16).padStart(8, '0');
  if (
    (bg.byteLength !== undefined && bg.byteLength !== bytes.length) ||
    (bg.crc32 !== undefined && bg.crc32 !== checksum)
  )
    return fail();
  return { byteLength: bytes.length, crc32: checksum };
}

export type MapImageLoad = {
  source: string;
  decoded: boolean;
  displayed: boolean;
  error: string;
};

/** Decoder and display events can arrive in either order; errors cannot be overwritten by late success events. */
export function updateMapImageLoad(
  previous: MapImageLoad | null,
  source: string,
  event: 'decoded' | 'displayed' | 'error',
  error = '',
): MapImageLoad {
  const state =
    previous?.source === source
      ? previous
      : { source, decoded: false, displayed: false, error: '' };
  if (state.error) return state;
  if (event === 'error') return { ...state, error };
  return { ...state, [event]: true };
}
