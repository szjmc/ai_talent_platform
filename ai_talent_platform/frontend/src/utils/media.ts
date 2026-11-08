export function mediaUnavailableMessage(): string | null {
  if (!window.isSecureContext) {
    return '当前页面不是安全连接，浏览器禁止使用摄像头和麦克风。请联系邀约方提供受信任的 HTTPS 链接；localhost 仅适用于本机测试。';
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return '当前浏览器不支持摄像头或麦克风，请使用新版浏览器并检查设备权限。';
  }
  return null;
}

export function isInsecureInviteUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol !== 'https:';
  } catch {
    return true;
  }
}

export function interviewerInviteUrl(value: string): string {
  const url = new URL(value);
  url.pathname = `${url.pathname.replace(/\/$/, '')}/room`;
  url.searchParams.set('role', 'interviewer');
  return url.toString();
}
