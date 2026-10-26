abstract interface class PushApi {
  void dispose();

  Future<void> registerInstallation({
    required String installationId,
    required String platform,
    required String registrationToken,
  });

  Future<void> revokeInstallation({required String installationId});
}
