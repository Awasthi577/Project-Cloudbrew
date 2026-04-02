resource "azurerm_linux_virtual_machine" "vm_linux" {
  location = "eastus"
  name = "vm-linux"
  resource_group_name = "rg-demo"
  size = "Standard_B2s"
  admin_ssh_key {
    public_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDemo"
    username = "azureuser"
  }
  os_disk {
    caching = "ReadWrite"
    storage_account_type = "Standard_LRS"
  }
}
