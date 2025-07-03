from runware import Runware, IImageInference, IPromptEnhance

class RunwareImageGenerator:
    def __init__(self, api_key):
        self.api_key = api_key
        self.runware = Runware(api_key=self.api_key)
    
    async def generate_images(self, positive_prompt, model, num_results=1, 
                            negative_prompt="", height=512, width=512, lora=None):
        # The connection will be established when needed
        await self.runware.connect()
        request_image = IImageInference(
            positivePrompt=positive_prompt,
            model=model,
            numberResults=num_results,
            negativePrompt=negative_prompt,
            height=height,
            width=width,
            lora=lora
        )
        images = await self.runware.imageInference(requestImage=request_image)
        # The connection will automatically close after 120 seconds of inactivity
        return images

    async def print_image_urls(self, positive_prompt, model):
        images = await self.generate_images(positive_prompt, model)
        for image in images:
            print(f"Image URL: {image.imageURL}")
            
    async def disconnect(self):
        """Disconnect from Runware service."""
        # if hasattr(self, 'runware'):
        #     await self.runware.disconnect()
