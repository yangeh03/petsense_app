/**
 * 跨端 multipart 文件组装。
 * 原生（RN）：FormData part 用 {uri, name, type}（RN 原生网络层支持）。
 * Web（浏览器/react-native-web）：FormData 只接受 Blob/File，
 * 直接塞 {uri} 会抛 "Unsupported FormDataPart implementation"——先 fetch 成 Blob 再 append。
 * 通过 DOM 能力检测区分运行时，不依赖 react-native 导入（保持可在 Node 中单测）。
 */

export type UploadFile = {
  field: string;
  uri: string;
  name: string;
  type: string;
};

const IS_WEB_LIKE = typeof document !== 'undefined' && typeof fetch === 'function';

export async function appendFormFile(form: FormData, file: UploadFile): Promise<void> {
  if (!IS_WEB_LIKE) {
    // 原生分支：RN FormData polyfill 接受 {uri,name,type} part
    form.append(file.field, {
      uri: file.uri,
      name: file.name,
      type: file.type,
    } as unknown as string);
    return;
  }
  // Web 分支：uri → Blob → append（浏览器 FormData 兼容）
  const blob = await (await fetch(file.uri)).blob();
  form.append(file.field, blob, file.name);
}

/** 组装跨端上传表单：字符串字段 + 文件字段 */
export async function buildUploadForm(
  fields: Record<string, string>,
  files: UploadFile[],
): Promise<FormData> {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    form.append(key, value);
  }
  for (const file of files) {
    await appendFormFile(form, file);
  }
  return form;
}
