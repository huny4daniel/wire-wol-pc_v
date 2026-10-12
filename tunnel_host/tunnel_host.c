/*
 * WireGuard 터널 서비스 호스트 — wireguard-windows embeddable-dll-service
 * README의 예제를 그대로 옮긴 것.
 *
 * 서비스 관리자가 "wirewol-tunnel-host.exe /service <conf 경로>"로 실행하면
 * 같은 폴더의 tunnel.dll을 불러 WireGuardTunnelService()에 넘긴다 — 이 함수가
 * 스스로 StartServiceCtrlDispatcher를 호출해 서비스 수명 전체를 처리한다.
 * tunnel.dll은 wireguard.dll을 "자기 exe가 있는 폴더"에서 찾으므로 세 파일은
 * 반드시 같은 폴더에 있어야 한다(app/wireguard_tunnel.py가 그렇게 배치한다).
 */
#include <windows.h>
#include <wchar.h>

typedef BOOL(__cdecl *tunnel_service_proc)(LPCWSTR conf_file);

int wmain(int argc, wchar_t **argv)
{
	wchar_t path[MAX_PATH];
	wchar_t *slash;
	DWORD len;
	HMODULE lib;
	tunnel_service_proc proc;

	if (argc != 3 || wcscmp(argv[1], L"/service"))
		return 2;

	len = GetModuleFileNameW(NULL, path, MAX_PATH);
	if (!len || len >= MAX_PATH)
		return 3;
	slash = wcsrchr(path, L'\\');
	if (!slash || (size_t)(slash - path) + 12 >= MAX_PATH)
		return 3;
	wcscpy(slash + 1, L"tunnel.dll");

	lib = LoadLibraryExW(path, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
	if (!lib)
		return 4;
	proc = (tunnel_service_proc)GetProcAddress(lib, "WireGuardTunnelService");
	if (!proc)
		return 5;
	return proc(argv[2]) ? 0 : 1;
}
