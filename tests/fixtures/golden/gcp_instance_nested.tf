resource "google_compute_instance" "gce_nested" {
  machine_type = "e2-medium"
  name = "gce-nested"
  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }
  network_interface {
    subnetwork = "projects/demo/regions/us-central1/subnetworks/default"
    access_config {
      nat_ip = "34.1.2.3"
    }
  }
}
