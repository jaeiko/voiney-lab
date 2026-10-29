// The media type the upload route reads from a file's name (lane RX1), the
// same table as the old screen's protocolSourceMediaType: the server
// decides from the bytes; the screen only has to send a type it accepts.

export const SOURCE_MEDIA_TYPES: Record<string, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  heic: "image/heic",
  heif: "image/heic",
  txt: "text/plain",
};

export const ACCEPTED_EXTENSIONS = ".pdf,.docx,.jpg,.jpeg,.png,.heic,.heif,.txt";

export function sourceMediaType(file: Pick<File, "name" | "type">): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (SOURCE_MEDIA_TYPES[extension]) return SOURCE_MEDIA_TYPES[extension];
  const type = (file.type || "").toLowerCase();
  return Object.values(SOURCE_MEDIA_TYPES).includes(type) ? type : null;
}
