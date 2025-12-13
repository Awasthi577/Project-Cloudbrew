$filesToDelete = @(
    "LCF/cloud_adapters/terraform_adapter.py",
    "LCF/cloud_adapters/test_terraform_streaming.py",
    "LCF/cloud_adapters/test_manager_dispatch.py",
    "tests/test_terraform_adapter_unit.py",
    "tests/mocked/test_terraform_adapter.py"
)

Write-Host "Starting cleanup..." -ForegroundColor Cyan

foreach ($file in $filesToDelete) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "Deleted: $file" -ForegroundColor Green
    } else {
        Write-Host "Skipped (not found): $file" -ForegroundColor DarkGray
    }
}

Write-Host "Cleanup complete! You are now fully migrated to OpenTofu." -ForegroundColor Cyan