resource "aws_instance" "web_simple" {
  ami = "ami-12345678"
  instance_type = "t3.micro"
  tags = {
    env = "test"
    owner = "platform"
  }
}
