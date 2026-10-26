import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';
import type { ImagePickerAsset } from 'expo-image-picker';
import { Platform } from 'react-native';

export type UploadImage = { uri: string; name: string; type: string };

/** 上传前统一转成 JPEG 的长边上限；服务端另有 36MP 像素兜底。 */
export const UPLOAD_MAX_EDGE = 2048;
const JPEG_QUALITY = 0.85;

/**
 * 把相册/相机返回的图片规范成可上传的 JPEG。
 *
 * iOS 相册多选会原样返回 HEIC（mimeType 却标成 image/jpeg），服务端只收 JPG/PNG/WebP，
 * 直接上传会被拒；原图动辄 3–4K 分辨率也没必要全量上传。这里统一解码重编码并限制长边。
 * Web 没有原生解码管线，保留浏览器给的文件原样交给服务端校验。
 */
export async function normalizeForUpload(asset: ImagePickerAsset): Promise<UploadImage> {
  const fallback: UploadImage = {
    uri: asset.uri,
    name: asset.fileName ?? 'photo.jpg',
    type: asset.mimeType ?? 'image/jpeg',
  };
  if (Platform.OS === 'web') return fallback;
  const context = ImageManipulator.manipulate(asset.uri);
  const longest = Math.max(asset.width ?? 0, asset.height ?? 0);
  if (longest > UPLOAD_MAX_EDGE) {
    const scale = UPLOAD_MAX_EDGE / longest;
    context.resize({
      width: Math.round((asset.width ?? 0) * scale),
      height: Math.round((asset.height ?? 0) * scale),
    });
  }
  const rendered = await context.renderAsync();
  try {
    const saved = await rendered.saveAsync({ format: SaveFormat.JPEG, compress: JPEG_QUALITY });
    return { uri: saved.uri, name: 'photo.jpg', type: 'image/jpeg' };
  } finally {
    rendered.release();
  }
}
