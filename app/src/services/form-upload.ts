/**
 * 跨端 multipart 文件上传。
 *
 * Web（浏览器/react-native-web）：FormData 只接受 Blob/File，
 * 直接塞 {uri} 会抛 "Unsupported FormDataPart implementation"——先 fetch 成 Blob 再 append。
 *
 * 原生（RN）：必须走 XMLHttpRequest。Expo SDK 57 的全局 fetch 是 expo/fetch，
 * 它序列化 FormData 时只接受 string / Blob / 带 bytes() 的 part，不再支持 RN
 * 传统的 {uri,name,type} part（convertFormDataAsync 会抛
 * "Unsupported FormDataPart implementation"）；而 RN 的 Blob 又禁止从
 * ArrayBuffer/Uint8Array 构造（BlobManager.createFromParts 直接 throw），
 * 所以 JS 侧拼不出它能接受的 Blob。XHR 走 RCTNetworking 的原生 multipart，
 * 由原生层流式读取 file:// URI，是 RN 一直以来的标准上传通道。
 *
 * 通过 DOM 能力检测区分运行时，不依赖 react-native 导入（保持可在 Node 中单测）。
 */

export type UploadFile = {
  field: string;
  uri: string;
  name: string;
  type: string;
};

export const IS_WEB_LIKE = typeof document !== 'undefined' && typeof fetch === 'function';

export async function appendFormFile(form: FormData, file: UploadFile): Promise<void> {
  // uri → Blob → append（浏览器 FormData 兼容）
  const blob = await (await fetch(file.uri)).blob();
  form.append(file.field, blob, file.name);
}

/** 组装上传表单：字符串字段 + 文件字段（仅 Web 端使用） */
export async function buildUploadForm(
  fields: Record<string, string>,
  files: UploadFile[],
): Promise<FormData> {
  if (!IS_WEB_LIKE) {
    throw new Error('原生端请使用 sendMultipart 上传（expo/fetch 不支持 {uri} FormData part）');
  }
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    form.append(key, value);
  }
  for (const file of files) {
    await appendFormFile(form, file);
  }
  return form;
}

export type MultipartResponse = { status: number; body: string };

/** 原生端 multipart 上传：RN FormData{uri} + XMLHttpRequest，返回原始响应。 */
export function sendMultipart(
  url: string,
  init: {
    method?: string;
    headers?: Record<string, string>;
    fields: Record<string, string>;
    files: UploadFile[];
    timeoutMs?: number;
    signal?: AbortSignal;
  },
): Promise<MultipartResponse> {
  if (IS_WEB_LIKE) throw new Error('sendMultipart 仅用于原生端，Web 端请走 fetch + FormData');
  const form = new FormData();
  for (const [key, value] of Object.entries(init.fields)) {
    form.append(key, value);
  }
  for (const file of init.files) {
    form.append(file.field, {
      uri: file.uri,
      name: file.name,
      type: file.type,
    } as unknown as string);
  }
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(init.method ?? 'POST', url);
    for (const [key, value] of Object.entries(init.headers ?? {})) {
      xhr.setRequestHeader(key, value);
    }
    if (init.timeoutMs) xhr.timeout = init.timeoutMs;
    xhr.onload = () => resolve({ status: xhr.status, body: String(xhr.responseText ?? '') });
    xhr.onerror = () => reject(new TypeError('网络连接失败，请检查网络后重试'));
    xhr.ontimeout = () => reject(new Error('上传超时，请检查网络后重试'));
    if (init.signal) {
      if (init.signal.aborted) {
        reject(new Error('已取消'));
        return;
      }
      init.signal.addEventListener('abort', () => xhr.abort(), { once: true });
    }
    xhr.send(form);
  });
}
